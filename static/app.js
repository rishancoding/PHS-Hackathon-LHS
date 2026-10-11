const $ = id => document.getElementById(id);
const number = value => value == null || value === '' || typeof value === 'boolean' || !Number.isFinite(Number(value)) ? null : Number(value);
const format = (value, digits=0) => number(value) == null ? 'Unavailable' : Number(value).toLocaleString(undefined, {maximumFractionDigits:digits});
const money = value => number(value) == null ? 'Unavailable' : Number(value).toLocaleString(undefined, {style:'currency',currency:'USD'});
const withUnit = (value, unit, digits=0) => number(value) == null ? 'Unavailable' : `${format(value,digits)} ${unit}`;
const targetCoverage = (energy,target) => number(energy) == null || number(target) == null || Number(target)<=0 ? null : Number(energy)/Number(target)*100;
let currentResult, currentJob, activeRoof, view, originalView, dragging;
let planUpdateActive=false, pendingPanelModelId=null;
let excludedRoofIds=new Set(), comparisonIds=[], comparisonJob=null;
function busy(value) { $('analyze').disabled=value; $('demo-button').disabled=value; $('lookup-addresses').disabled=value; $('replan').disabled=value; $('include-all-roofs').disabled=value; $('budget-includes-installation').disabled=value; document.querySelectorAll('#roof-selection-options input').forEach(input=>input.disabled=value); }
function error(message) { $('error').textContent=message; $('error').hidden=false; }
function planInputs() {
  return {budget_usd:Number($('budget').value), budget_includes_installation:$('budget-includes-installation').checked, energy_target_kwh_year:Number($('energy-target').value),
    space_limit_m2:$('space-limit').value.trim() ? Number($('space-limit').value) : null, priority:$('panel-priority').value,
    excluded_roof_ids:[...excludedRoofIds]};
}
async function analyze(demo=false) {
  window.roofreadyAddress?.close();
  window.roofreadyLoading.start(demo ? 'Synthetic roofs and sunlight data' : $('address').value.trim(),demo);
  $('error').hidden=true; busy(true);
  $('status').textContent=demo ? 'Building a synthetic demo…' : 'Starting your analysis…';
  const newSearchInputs={...planInputs(), excluded_roof_ids:[]};
  const payload = demo ? {demo:true, ...newSearchInputs} : {
    address:$('address').value.trim(), radius:Number($('radius').value), unit:$('unit').value,
    usable_fraction:Number($('usable').value), panel_kw_per_m2:Number($('density').value), api_key:$('api-key').value, ...newSearchInputs
  };
  try {
    const response=await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok) throw new Error(data.error || 'Could not start analysis.');
    $('api-key').value='';
    currentJob=data.job_id;
    await poll(currentJob);
  } catch(e) { error(e.message); $('status').textContent='Analysis could not finish.'; }
  finally { busy(false);window.roofreadyLoading.finish(); }
}
async function poll(job) {
  while(true) {
    const response=await fetch(`/api/jobs/${job}`);
    if(!response.ok) throw new Error('Could not read analysis status.');
    const state=await response.json(); $('status').textContent=state.message;window.roofreadyLoading.update(state.message);
    if(state.status==='failed') throw new Error(state.message);
    if(state.status==='complete') { render(state.result,job); await loadHistory(); return; }
    await new Promise(resolve=>setTimeout(resolve,400));
  }
}
function pathFor(geometry) {
  const polygons = geometry.type==='Polygon' ? [geometry.coordinates] : geometry.coordinates;
  return polygons.map(polygon=>polygon.map(ring=>'M'+ring.map(point=>point.join(',')).join('L')+'Z').join('')).join('');
}
function setView() { $('roof-map').setAttribute('viewBox',view.join(' ')); }
function selectRoof(id) {
  $('map-section').open=true;
  activeRoof=id;
  $('roof-results').open=true;
  document.querySelectorAll('details.roof-accordion').forEach(detail=>{if(detail.dataset.roof===id)detail.open=true;});
  document.querySelectorAll('[data-roof]').forEach(element=>element.classList.toggle('selected',element.dataset.roof===id));
  const roof=currentResult?.roofs.find(roof=>roof.properties.id===id);
  if(roof){$('selected-roof').hidden=false;$('selected-address').textContent=roofLabel(roof.properties);$('selected-roof-info').textContent=`${roof.properties.id} · ${format(roof.properties.area_m2,1)} m² footprint · ${format(roof.properties.estimated_energy_kwh_year)} kWh/year estimated potential. ${addressNote(roof.properties)}`;}
}
function roofLabel(p){return p.address || 'Address unavailable';}
function addressNote(p){return p.address_status==='synthetic' ? 'Synthetic demo address.' : p.address_status==='nearby_address' ? 'Nearby address; roof match is approximate.' : p.address_status==='reverse_geocoded' ? 'Reverse-geocoded address; verify the building match.' : p.address_status==='not_looked_up' ? 'Street address not looked up yet. Use Look up roof addresses.' : 'Address not resolved. Use Look up roof addresses.';}
function render(result,job,preserveView=false) {
  currentResult=result; currentJob=job; activeRoof=null;
  $('selected-roof').hidden=true;
  $('empty').hidden=true; $('intro').hidden=true; $('result-nav').hidden=false;
  $('result-content').hidden=false; $('result-details').hidden=false;
  if(!preserveView)$('search-controls').open=false;
  $('budget-includes-installation').checked=result.proposal?.budget_includes_installation!==false;renderBudgetScope();
  renderRoofSelection(result);
  $('source-badge').textContent=result.demo ? 'Sample area · Synthetic data' : 'Google Solar · Screening estimates';
  const seconds=result.search_seconds;
  $('search-time').hidden=typeof seconds!=='number';
  if(typeof seconds==='number')$('search-time').textContent=`Search took ${seconds<60 ? `${seconds.toFixed(1)} s` : `${Math.floor(seconds/60)} min ${Math.round(seconds%60)} s`}`;
  $('result-address').textContent=result.request.address;
  $('result-meta').textContent=`${format(result.request.radius_meters,2)} m radius · ${result.request.latitude.toFixed(5)}, ${result.request.longitude.toFixed(5)} · ${format(result.analysis.resolution_m,2)} m analysis pixels`;
  $('roof-count').textContent=format(result.summary.candidate_roofs);
  $('total-energy').textContent=format(result.summary.estimated_energy_kwh_year);
  $('coverage').textContent=format(result.analysis.flux_coverage_percent,1)+'%';
  for(const [id,file] of [['csv-export','roofs.csv'],['geojson-export','roofs.geojson'],['json-export','results.json']]) $(''+id).href=`/results/${job}/${file}`;
  const width=result.analysis.raster_width,height=result.analysis.raster_height;
  originalView=[0,0,width,height]; if(!preserveView || !view) view=[...originalView]; setView();
  for(const [id,file] of [['aerial','aerial.png'],['sunlight','sunlight.png']]) {
    const image=$(id); image.setAttribute('href',`/results/${job}/${file}`); image.setAttribute('width',width); image.setAttribute('height',height);
  }
  $('center-marker').setAttribute('transform',`translate(${width/2} ${height/2})`);
  if(!preserveView) {$('heat-toggle').checked=false; $('sunlight').setAttribute('visibility','hidden');}
  $('outlines').replaceChildren(); $('roof-rows').replaceChildren(); $('roof-accordions').replaceChildren();
  $('existing-panels').replaceChildren();
  for(const feature of result.existing_panels || []) {
    const area=document.createElementNS('http://www.w3.org/2000/svg','path');
    area.setAttribute('d',pathFor(feature.preview_geometry)); area.setAttribute('fill-rule','evenodd'); area.setAttribute('class','existing-panel');
    $('existing-panels').append(area);
  }
  for(const roof of result.roofs) {
    const p=roof.properties;
    const outline=document.createElementNS('http://www.w3.org/2000/svg','path');
    outline.setAttribute('d',pathFor(roof.preview_geometry)); outline.setAttribute('fill-rule','evenodd');
    outline.setAttribute('class',excludedRoofIds.has(p.id) ? 'roof-outline excluded' : 'roof-outline'); outline.dataset.roof=p.id;
    outline.setAttribute('tabindex','0'); outline.setAttribute('role','button'); outline.setAttribute('aria-label',`${roofLabel(p)}, ${format(p.estimated_energy_kwh_year)} kWh per year`);
    const title=document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent=`${roofLabel(p)} · ${addressNote(p)}`;outline.append(title);
    outline.addEventListener('click',()=>selectRoof(p.id));
    outline.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectRoof(p.id);}});
    $('outlines').append(outline);
    const row=document.createElement('tr'); row.dataset.roof=p.id; row.tabIndex=0;
    const values=[`${p.rank}. ${roofLabel(p)}`,format(p.area_m2,1),format(p.annual_yield_kwh_per_kw,1),format(p.estimated_capacity_kw,1),format(p.estimated_energy_kwh_year),format(p.flux_coverage_percent,1)+'%'];
    values.forEach((value,index)=>{ const cell=document.createElement('td'); cell.textContent=value;
      if(index===0){const detail=document.createElement('small');detail.textContent=`${p.id} · ${addressNote(p)}${p.partial_roof?' Partial roof at boundary.':''}${p.existing_panel_area_m2?` About ${format(p.existing_panel_area_m2,1)} m² possible existing panels (avoided).`:''}`;cell.append(detail);} row.append(cell); });
    row.addEventListener('click',()=>selectRoof(p.id)); row.addEventListener('keydown',event=>{if(event.key==='Enter'){selectRoof(p.id);}});
    $('roof-rows').append(row);
    const detail=document.createElement('details');detail.className='roof-accordion';detail.dataset.roof=p.id;
    const summary=document.createElement('summary');
    const heading=document.createElement('span');heading.textContent=`${p.rank}. ${roofLabel(p)}`;
    const badge=document.createElement('span');badge.className='roof-energy-badge';badge.textContent=`${format(p.estimated_energy_kwh_year)} kWh/year potential`;
    summary.append(heading,badge);detail.append(summary);
    const body=document.createElement('div');body.className='roof-detail-body';
    const info=document.createElement('p');info.className='hint';info.textContent=`${p.id} · ${addressNote(p)}${p.partial_roof?' Partial roof at boundary.':''}${p.existing_panel_area_m2?` About ${format(p.existing_panel_area_m2,1)} m² possible existing panels (avoided).`:''}`;
    const metrics=document.createElement('div');metrics.className='roof-detail-metrics';
    for(const [label,value] of [['Footprint',`${format(p.area_m2,1)} m²`],['Assumed usable area',withUnit(number(p.area_m2)==null || number(result.assumptions?.usable_roof_fraction)==null ? null : p.area_m2*result.assumptions.usable_roof_fraction,'m²',1)],['Average yield',`${format(p.annual_yield_kwh_per_kw,1)} kWh/kW/year`],['Potential capacity',`${format(p.estimated_capacity_kw,1)} kW`],['Sunlight coverage',`${format(p.flux_coverage_percent,1)}%`]]){
      const metric=document.createElement('div');const labelNode=document.createElement('span');labelNode.textContent=label;const valueNode=document.createElement('strong');valueNode.textContent=value;metric.append(labelNode,valueNode);metrics.append(metric);
    }
    const planned=(result.proposal?.panels || []).filter(panel=>panel.roof_id===p.id);
    const planNote=document.createElement('p');planNote.textContent=planned.length ? `${planned.length} blue panels plotted on this roof, with ${format(planned.reduce((sum,panel)=>sum+panel.estimated_energy_kwh_year,0))} kWh/year of estimated output.` : 'No proposed panels are plotted on this roof in the current plan.';
    const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='Highlight roof on map';button.addEventListener('click',()=>{selectRoof(p.id);$('roof-map').scrollIntoView({behavior:'smooth',block:'center'});});
    body.append(info,metrics,planNote,button);detail.append(body);$('roof-accordions').append(detail);
  }
  $('no-roofs').hidden=result.roofs.length>0;
  renderProposal(result.proposal);
  renderRecommendations(result.panel_recommendations);
  renderDashboard(result);
  renderCatalogTable(result.panel_recommendations);
  $('findings').textContent=result.report?.findings || 'Update the panel plan to generate a summary for this saved search.';
  $('next-steps').textContent=result.report?.next_steps || 'Verify roof addresses and usable area with a qualified solar installer before proceeding.';
  const a=result.assumptions;
  $('assumptions').textContent=`Assumptions: ${format(a.usable_roof_fraction*100)}% usable footprint · ${a.panel_kw_per_m2} kW/m² panel capacity · ${a.minimum_roof_area_m2} m² minimum candidate area. Ranked by estimated annual energy.`;
  const date=result.imagery?.imageryDate;
  $('imagery-date').textContent=date ? `· ${date.year}-${date.month}-${date.day}` : '';
  $('imagery-source').textContent=result.demo ? 'Synthetic imagery' : 'Imagery: Google Solar API';
}
function renderProposal(proposal) {
  renderPlottedPanelReport(proposal);
  $('proposed-panels').replaceChildren();
  $('panel-map-scale').textContent=proposal ? `${proposal.panel_model ? panelName(proposal.panel_model)+' · ' : ''}${format(proposal.panel_width_m,5)} × ${format(proposal.panel_height_m,5)} m per panel, drawn to scale (${format(currentResult.analysis.resolution_m,3)} m per image pixel). Modules may be rotated 90° to fit.` : 'Choose a panel recommendation to preview its footprint.';
  $('panel-count').textContent=proposal ? format(proposal.panel_count) : '—';
  $('plan-cost').textContent=proposal ? '$'+format(proposal.estimated_cost_usd,2) : '—';
  $('plan-energy').textContent=proposal ? format(proposal.estimated_energy_kwh_year) : '—';
  $('plan-status').textContent=proposal ? proposal.status+(proposal.energy_shortfall_kwh_year>0 ? ` Shortfall: ${format(proposal.energy_shortfall_kwh_year)} kWh/year.` : '') : 'Set your budget and annual energy target, then click Update recommendations to create a plan from this saved analysis.';
  $('plan-status').classList.toggle('target-met',Boolean(proposal?.target_met));
  $('plan-notes').textContent=proposal ? `${proposal.panel_model ? proposal.panel_model.manufacturer+' '+proposal.panel_model.model_name+' · ' : ''}${proposal.panel_width_m} × ${proposal.panel_height_m} m panels · ${format(proposal.panel_kw,2)} kW each · estimated $${format(proposal.installed_cost_per_watt,3)}/W installed. ${proposal.notes}` : '';
  if(proposal?.installation_estimate){const estimate=proposal.installation_estimate;const range=proposal.estimated_cost_range_usd;$('plan-notes').textContent+=` Geographic estimate: ${estimate.state || 'unknown state'} · ${estimate.region}, $${format(estimate.installed_cost_per_watt,3)}/W; estimated total range $${format(range[0])}–$${format(range[1])}. ${estimate.notes}`;}
  if(!proposal)return;
  for(const panel of proposal.panels) {
    const outline=document.createElementNS('http://www.w3.org/2000/svg','path');
    outline.setAttribute('d',pathFor(panel.preview_geometry)); outline.setAttribute('class','proposed-panel');
    outline.dataset.model=panel.model_id || '';
    // Keep the visible outline proportional to the footprint, even for tiny modules.
    outline.style.strokeWidth=String(.015/currentResult.analysis.resolution_m);
    const title=document.createElementNS('http://www.w3.org/2000/svg','title');
    const roof=currentResult.roofs.find(roof=>roof.properties.id===panel.roof_id);
    title.textContent=`${proposal.panel_model ? panelName(proposal.panel_model) : 'Proposed panel'} · ${format(panel.width_m ?? proposal.panel_width_m,5)} × ${format(panel.height_m ?? proposal.panel_height_m,5)} m on map · ${roof ? roofLabel(roof.properties) : panel.roof_id} · ${format(panel.estimated_energy_kwh_year)} kWh/year`;
    outline.append(title); outline.addEventListener('click',()=>selectRoof(panel.roof_id)); $('proposed-panels').append(outline);
  }
}
function renderPlottedPanelReport(proposal) {
  $('plotted-roofs').replaceChildren();
  $('placement-explanation').textContent=proposal?.placement_explanation || 'The planner uses roof-average annual yield and geometric fit. Update recommendations to generate detailed reasoning for this saved layout.';
  if(!proposal){$('plotted-panel-summary').textContent='Generate a panel plan using your budget and annual energy target to see proposed panels and their roof-by-roof breakdown.';return;}
  const groups=new Map();
  for(const panel of proposal.panels){const group=groups.get(panel.roof_id)||{count:0,energy:0};group.count++;group.energy+=panel.estimated_energy_kwh_year;groups.set(panel.roof_id,group);}
  const remaining=proposal.budget_usd-(proposal.budget_cost_usd ?? proposal.estimated_cost_usd);
  $('plotted-panel-summary').textContent=`The image shows ${format(proposal.panel_count)} proposed ${proposal.panel_width_m} × ${proposal.panel_height_m} m panels across ${groups.size} roof region${groups.size===1?'':'s'}. Together, they are estimated to produce ${format(proposal.estimated_energy_kwh_year)} kWh/year for an estimated installed cost of $${format(proposal.estimated_cost_usd)}, leaving $${format(remaining)} of your ${proposal.budget_includes_installation===false ? 'module-hardware' : 'installed-system'} budget.${proposal.budget_includes_installation===false ? ' Other equipment and installation cost extra.' : ''} ${proposal.target_met?'The estimated annual output meets your target.':`The target remains unmet by ${format(proposal.energy_shortfall_kwh_year)} kWh/year.`}${!proposal.panel_count?' No panels fit the current budget and available roof assumptions.':''}`;
  for(const [id,group] of groups){
    const roof=currentResult.roofs.find(roof=>roof.properties.id===id);
    const row=document.createElement('div');row.className='plotted-roof-row';
    const paragraph=document.createElement('p');paragraph.textContent=`${roof?roofLabel(roof.properties):id} (${id}): ${format(group.count)} panels · ${format(group.energy)} kWh/year · $${format(group.count*proposal.panel_kw*1000*proposal.installed_cost_per_watt)} estimated cost.`;
    const explanation=proposal.roof_placement_reasons?.find(reason=>reason.roof_id===id);
    if(explanation)paragraph.textContent+=` ${explanation.reason}`;
    const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='View roof';button.addEventListener('click',()=>{selectRoof(id);$('roof-map').scrollIntoView({behavior:'smooth',block:'center'});});row.append(paragraph,button);$('plotted-roofs').append(row);
  }
}
function panelName(model){return `${model.manufacturer} ${model.model_name}`;}
function panelDescription(item) {
  const model=item.model;
  const bifacial=String(model.bifacial).toLowerCase()==='true' ? 'bifacial ' : '';
  return `A ${format(model.rated_power_w)} W ${bifacial}rigid solar module measuring ${format(model.width_m,3)} × ${format(model.height_m,3)} m, with a listed hardware price of $${format(model.price_usd,2)} per panel. Your current layout uses ${format(item.panel_count)} modules for an estimated ${format(item.estimated_energy_kwh_year)} kWh/year.`;
}
function panelScenario(item,items) {
  const m=item.model;
  if(!item.panel_count)return `Possible use: reconsider this model if your budget or usable roof space increases. One module needs ${format(m.area_m2,2)} m² of panel area plus spacing, and costs about $${format(item.estimated_installed_cost_per_panel_usd,2)} installed under this estimate.`;
  const scenarios=[];
  const lowPrice=Math.min(...items.map(r=>r.model.price_per_watt_usd));
  const highDensity=Math.max(...items.map(r=>r.model.power_density_w_per_m2));
  if(Math.abs(m.price_per_watt_usd-lowPrice)<1e-8)scenarios.push('you want the lowest listed hardware price per watt in this catalog');
  if(Math.abs(m.power_density_w_per_m2-highDensity)<1e-8)scenarios.push('your roof space is tight and you want the most rated power per square meter among the compared models');
  if(m.rated_power_w<=60)scenarios.push('you have narrow usable roof sections or a small energy need and can accommodate the extra module connections');
  if(m.product_warranty_years)scenarios.push(`you value documented defect coverage (${format(m.product_warranty_years)}-year product warranty${m.performance_warranty_years ? ` and ${format(m.performance_warranty_years)}-year output warranty` : ''})`);
  if(!scenarios.length)scenarios.push(`the ${format(m.width_m,3)} × ${format(m.height_m,3)} m footprint suits your available roof sections and you want ${format(m.rated_power_w)} W modules`);
  return `Possible use: ${scenarios.slice(0,2).join('; or ')}.${item.target_met ? '' : ` This layout could offset part of your electricity use, but still leaves ${format(item.energy_shortfall_kwh_year)} kWh/year of your target unmet.`}`;
}
function setPanelLink(link,url) {
  link.hidden=true;link.removeAttribute('href');
  try{const target=new URL(url);if(['https:','http:'].includes(target.protocol)){link.href=target.href;link.hidden=false;}}catch(e){/* Missing catalog product URL. */}
}
function renderRecommendations(data) {
  const expandedModels=new Set([...document.querySelectorAll('details.panel-option[open]')].map(card=>card.dataset.model));
  $('recommendation-main').replaceChildren();$('recommendation-tiers').replaceChildren();
  $('recommendation-excluded').textContent=''; $('recommendation-notes').textContent='';
  $('chosen-panel-link').hidden=true;$('recommendation-others').hidden=!data;
  if(!data){
    $('panel-comparison-section').hidden=true;comparisonIds=[];comparisonJob=currentJob;updatePdfLink();
    const model=currentResult?.proposal?.panel_model;
    $('chosen-panel-name').textContent=model ? panelName(model) : 'Your proposed solar layout';
    $('chosen-panel-label').textContent='PANEL PLOTTED ON THE MAP';
    $('chosen-panel-description').textContent='Click Update recommendations to compare the CSV catalog and choose a panel for this saved search.';
    if(model)setPanelLink($('chosen-panel-link'),model.specifications_source_url || model.price_source_url);
    $('recommendation-summary').textContent='Click Update recommendations to compare the CSV catalog using this saved search.';return;
  }
  $('budget-includes-installation').checked=data.budget_includes_installation!==false;renderBudgetScope();
  $('budget').value=data.budget_usd; $('energy-target').value=data.energy_target_kwh_year;
  $('space-limit').value=data.space_limit_m2 ?? ''; $('panel-priority').value=data.priority;
  const best=data.recommendations.find(item=>item.recommended) || data.recommendations[0];
  const chosen=data.recommendations.find(item=>item.model.model_id===data.selected_model_id) || best;
  if(chosen){
    $('chosen-panel-name').textContent=panelName(chosen.model);
    $('chosen-panel-label').textContent=chosen.recommended ? '#1 recommendation · Shown on map' : `Selected panel · Rank ${chosen.rank}`;
    $('chosen-panel-description').textContent=`${format(chosen.model.rated_power_w)} W ${String(chosen.model.bifacial).toLowerCase()==='true' ? 'bifacial ' : ''}rigid module · ${format(chosen.model.width_m,3)} × ${format(chosen.model.height_m,3)} m. Dimensions and hardware price come from the panel catalog.`;
    setPanelLink($('chosen-panel-link'),chosen.model.specifications_source_url || chosen.model.price_source_url);
  }
  const available=best?.available_roof_area_m2 ?? 0;
  $('recommendation-context').textContent=`Comparing ${data.recommendations.length} rigid models using a $${format(data.budget_usd)} ${data.budget_includes_installation===false?'module-hardware':'installed-system'} budget and ${format(data.energy_target_kwh_year)} kWh/year. Detected usable roof area: ${format(available,1)} m²${data.space_limit_m2 != null ? `; module-area cap: ${format(data.space_limit_m2,1)} m²` : ''}. ${best?.target_met ? 'At least one model meets your target under these assumptions.' : 'No compared model meets your target under the current constraints.'} Click a recommendation row to preview its panel dimensions and layout on the map.`;
  $('recommendation-summary').textContent=`${data.recommendations.length} panel models · ${excludedRoofIds.size} roofs excluded · ${best?.target_met ? 'Target achievable under these assumptions' : 'Target not met under current constraints'}. Choose an alternative to preview it on the map.`;
  const labels={1:'Tier 1 · Target met',2:'Tier 2 · Partial output',3:'Tier 3 · No current fit'};
  const others=data.recommendations.filter(item=>item!==best);
  $('recommendation-others-label').textContent=`View other panel suggestions (${others.length})`;
  $('recommendation-others').hidden=others.length===0;
  for(const item of [best,...others].filter(Boolean)){
    const model=item.model;const selected=model.model_id===data.selected_model_id;
    const featured=item===best;
    const card=document.createElement(featured?'article':'details');card.className=featured?'featured-panel':'panel-option roof-accordion';
    card.classList.toggle('selected',selected);card.dataset.model=model.model_id;
    if(!featured)card.open=selected || expandedModels.has(model.model_id);
    card.addEventListener('click',event=>{
      if(event.target.closest('a,button,.panel-evidence'))return;
      if(!selected || planUpdateActive)previewPanel(model.model_id);
    });
    const title=document.createElement(featured?'h3':'summary');
    const name=document.createElement('span');name.textContent=`${item.rank}. ${panelName(model)}`;
    const badge=document.createElement('span');badge.className=`panel-tier-badge tier-${item.tier}`;badge.textContent=`${labels[item.tier]}${selected?' · Plotted on map':''}`;
    if(featured){
      const previewTitle=document.createElement('button');previewTitle.type='button';previewTitle.className='panel-preview-title';previewTitle.append(name);
      previewTitle.setAttribute('aria-label',`Preview ${panelName(model)} on the map`);previewTitle.addEventListener('click',()=>previewPanel(model.model_id));title.append(previewTitle,badge);
    }else{title.append(name,badge);title.setAttribute('aria-label',`Expand and preview ${panelName(model)} on the map`);}
    card.append(title);
    const body=document.createElement('div');body.className='panel-option-body';
    const description=document.createElement('p');description.className='panel-description';description.textContent=panelDescription(item);
    const product=document.createElement('a');product.className='text-link';product.textContent='View this solar panel ↗';product.target='_blank';product.rel='noopener noreferrer';setPanelLink(product,model.specifications_source_url || model.price_source_url);
    const specificationDetails=document.createElement('details');specificationDetails.className='panel-specifications';
    const specificationSummary=document.createElement('summary');specificationSummary.textContent='Module specifications and hardware pricing';specificationDetails.append(specificationSummary);
    const stats=document.createElement('div');stats.className='roof-detail-metrics panel-detail-metrics';
    const specifications=document.createElement('div');specifications.className='roof-detail-metrics panel-spec-metrics';
    for(const [label,value] of [['Module power',`${format(model.rated_power_w)} W`],['Dimensions',`${format(model.width_m,3)} × ${format(model.height_m,3)} m`],['Hardware / panel',`$${format(model.price_usd,2)}`],['Power / area',`${format(model.power_density_w_per_m2,1)} W/m²`],['Panels in layout',format(item.panel_count)],['Module area used',`${format(item.panel_area_used_m2,1)} m²`],['Annual output',`${format(item.estimated_energy_kwh_year)} kWh/year`],['Installed estimate',`$${format(item.estimated_cost_usd,2)}`]]){
      const metric=document.createElement('div');const labelNode=document.createElement('span');labelNode.textContent=label;const valueNode=document.createElement('strong');valueNode.textContent=value;metric.append(labelNode,valueNode);
      metric.className=label.includes('cost') || label.includes('estimate') || label.includes('Hardware') ? 'metric-cost' : label.includes('output') ? 'metric-energy' : label.includes('area') || label==='Dimensions' ? 'metric-space' : 'metric-power';
      (['Panels in layout','Module area used','Annual output','Installed estimate'].includes(label) ? stats : specifications).append(metric);
    }
    specificationDetails.append(specifications);
    const scenario=document.createElement('p');scenario.className='panel-scenario';scenario.textContent=panelScenario(item,data.recommendations);
    const status=document.createElement('p');status.textContent=item.target_met ? 'This layout meets your annual energy target under the current assumptions.' : `Energy shortfall: ${format(item.energy_shortfall_kwh_year)} kWh/year.`;
    const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=selected?'Currently plotted':'Preview this panel on the map';button.disabled=selected;
    button.addEventListener('click',()=>previewPanel(model.model_id));
    const detail=document.createElement('details');detail.className='panel-evidence';const detailTitle=document.createElement('summary');detailTitle.textContent='Cost breakdown, warranties and comparison reasons';detail.append(detailTitle);
    const cost=document.createElement('p');cost.className='hint';cost.textContent=`$${format(item.estimated_panel_hardware_cost_usd,2)} listed module hardware + $${format(item.estimated_other_equipment_installation_cost_usd,2)} estimated other equipment and installation. Cost range: $${format(item.estimated_cost_range_usd[0])}–$${format(item.estimated_cost_range_usd[1])}. Price checked ${model.price_date || 'date unavailable'}.`;
    const reasons=document.createElement('ul');for(const reason of item.reasons){const li=document.createElement('li');li.textContent=reason;reasons.append(li);}
    detail.append(cost,reasons);
    if(model.notes){const note=document.createElement('p');note.className='hint';note.textContent=model.notes;detail.append(note);}
    body.append(description,product,stats,scenario,status,button,specificationDetails,detail);card.append(body);
    $(featured?'recommendation-main':'recommendation-tiers').append(card);
  }
  $('recommendation-excluded').textContent=data.excluded_models.map(model=>`${model.model_name}: ${model.reason}`).join(' ');
  renderPanelComparison(data);
  $('recommendation-notes').textContent=data.notes+' Product warranty covers specified defects; performance warranty covers specified output. Neither establishes physical durability or actual lifespan. Missing specifications are unknown.';
}
async function previewPanel(modelId) {
  if(currentResult?.proposal?.panel_model?.model_id===modelId && !planUpdateActive)return;
  await updatePlan(modelId);
}
async function updatePlan(selectedModelId=null) {
  if(!currentJob)return false;
  if(planUpdateActive){
    // Serialize previews so rapid clicks finish with the last requested model.
    if(selectedModelId)pendingPanelModelId=selectedModelId;
    return false;
  }
  if($('replan').disabled)return false;
  planUpdateActive=true;busy(true);$('history').disabled=true;
  $('error').hidden=true;$('roof-map').setAttribute('aria-busy','true');
  const item=currentResult?.panel_recommendations?.recommendations.find(item=>item.model.model_id===selectedModelId);
  $('panel-map-scale').textContent=item ? `Updating preview: ${panelName(item.model)} · ${format(item.model.width_m,5)} × ${format(item.model.height_m,5)} m…` : 'Updating panel layout…';
  $('status').textContent='Updating the panel preview using the existing roof analysis…';
  let success=false;
  try {
    const response=await fetch(`/api/plan/${encodeURIComponent(currentJob)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...planInputs(), selected_model_id:selectedModelId})});
    const result=await response.json(); if(!response.ok)throw new Error(result.error || 'Could not update the plan.');
    render(result,currentJob,true);success=true;
    $('status').textContent=`${result.proposal.panel_model ? panelName(result.proposal.panel_model)+' preview' : 'Panel layout'} updated. No new Google API requests.`;
  } catch(e){
    error(e.message);$('status').textContent='Preview could not update; the previous panel layout is retained.';$('budget-includes-installation').checked=currentResult.proposal?.budget_includes_installation!==false;renderBudgetScope();renderRoofSelection(currentResult);renderProposal(currentResult.proposal);
  } finally {
    planUpdateActive=false;busy(false);$('history').disabled=false;$('roof-map').setAttribute('aria-busy','false');
  }
  const next=pendingPanelModelId;pendingPanelModelId=null;
  if(next)await previewPanel(next);
  return success;
}
function zoom(factor,anchor) {
  if(!view) return;
  const nextWidth=Math.max(originalView[2]/16,Math.min(originalView[2]*2,view[2]*factor));
  const nextHeight=nextWidth*originalView[3]/originalView[2];
  const center=anchor || {x:view[0]+view[2]/2,y:view[1]+view[3]/2};
  const ratioX=(center.x-view[0])/view[2],ratioY=(center.y-view[1])/view[3];
  view=[center.x-ratioX*nextWidth,center.y-ratioY*nextHeight,nextWidth,nextHeight]; setView();
}
$('analysis-form').addEventListener('submit',event=>{event.preventDefault();analyze();});
$('demo-button').addEventListener('click',()=>analyze(true));
$('heat-toggle').addEventListener('change',()=> $('sunlight').setAttribute('visibility',$('heat-toggle').checked?'visible':'hidden'));
$('zoom-in').addEventListener('click',()=>zoom(.75)); $('zoom-out').addEventListener('click',()=>zoom(1/.75));
$('reset-map').addEventListener('click',()=>{if(originalView){view=[...originalView];setView();}});
$('replan').addEventListener('click',()=>updatePlan());
$('lookup-addresses').addEventListener('click',async()=>{
  if(!currentJob)return;busy(true);$('error').hidden=true;
  try{const response=await fetch(`/api/addresses/${encodeURIComponent(currentJob)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:$('api-key').value})});const data=await response.json();if(!response.ok)throw new Error(data.error || 'Could not look up addresses.');$('api-key').value='';await poll(currentJob);}catch(e){error(e.message);}finally{busy(false);}
});
function mapPoint(event){const point=new DOMPoint(event.clientX,event.clientY);return point.matrixTransform($('roof-map').getScreenCTM().inverse());}
$('roof-map').addEventListener('wheel',event=>{if(!view)return; event.preventDefault(); zoom(Math.exp(Math.max(-.35,Math.min(.35,event.deltaY*.002))),mapPoint(event));},{passive:false});
$('roof-map').addEventListener('dblclick',event=>{if(!view)return;event.preventDefault();zoom(.5,mapPoint(event));});
const pointers=new Map(); let pinchDistance;
$('roof-map').addEventListener('pointerdown',event=>{
  if(!view)return;
  pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
  if(pointers.size===2){dragging=null; const [a,b]=[...pointers.values()];pinchDistance=Math.hypot(a.x-b.x,a.y-b.y);}
  else if(!event.target.classList.contains('roof-outline')&&!event.target.classList.contains('proposed-panel'))dragging=mapPoint(event);
  if(pointers.size>1 || (!event.target.classList.contains('roof-outline')&&!event.target.classList.contains('proposed-panel'))) $('roof-map').setPointerCapture(event.pointerId);
});
$('roof-map').addEventListener('pointermove',event=>{
  if(!pointers.has(event.pointerId))return;
  pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
  if(pointers.size===2){const [a,b]=[...pointers.values()];const distance=Math.hypot(a.x-b.x,a.y-b.y);if(distance>0&&pinchDistance>0)zoom(pinchDistance/distance,mapPoint({clientX:(a.x+b.x)/2,clientY:(a.y+b.y)/2}));pinchDistance=distance;}
});
$('roof-map').addEventListener('pointermove',event=>{if(!dragging)return;const point=mapPoint(event);view[0]+=dragging.x-point.x;view[1]+=dragging.y-point.y;setView();dragging=mapPoint(event);});
function endPointer(event){pointers.delete(event.pointerId);dragging=null;pinchDistance=null;}
$('roof-map').addEventListener('pointerup',endPointer);$('roof-map').addEventListener('pointercancel',endPointer);
async function loadHistory() {
  try {
    const response=await fetch('/api/history'); if(!response.ok)return;
    const runs=await response.json();
    window.roofreadyAddress?.setRecent(runs.filter(run=>!run.demo).map(run=>run.address));
    $('history').replaceChildren(new Option('Choose an analysis',''));
    for(const run of runs) $('history').append(new Option(`${run.demo?'Demo · ':''}${run.address}`,run.job_id));
    $('history-wrap').hidden=runs.length===0;
  } catch(e) { /* Search remains usable if saved analysis listing is unavailable. */ }
}
$('history').addEventListener('change',async()=>{
  const job=$('history').value; if(!job)return;
  try { const response=await fetch(`/api/jobs/${encodeURIComponent(job)}`); if(!response.ok)throw new Error('Saved analysis is unavailable.');
    const state=await response.json(); render(state.result,job); $('status').textContent='Saved analysis loaded. No new API requests.';
  } catch(e){error(e.message);}
});
loadHistory();

function renderRoofSelection(result) {
  excludedRoofIds=new Set(result.roof_selection?.excluded_roof_ids || []);
  $('roof-selection-section').hidden=result.roofs.length===0;
  $('roof-selection-summary').textContent=`Roofs in this plan · ${result.roofs.length-excludedRoofIds.size} of ${result.roofs.length} included`;
  $('roof-selection-options').replaceChildren();
  for(const [index,roof] of result.roofs.entries()){
    const p=roof.properties;
    const row=document.createElement('div');row.className='roof-selection-row';
    const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.checked=!excludedRoofIds.has(p.id);input.id=`choose-roof-${index}`;input.dataset.roofId=p.id;input.disabled=planUpdateActive;
    const text=document.createElement('span');const name=document.createElement('strong');name.textContent=roofLabel(p);
    const stats=document.createElement('small');stats.textContent=`${p.id} · ${format(p.area_m2,1)} m² · ${format(p.annual_yield_kwh_per_kw)} kWh/kW/year${p.address_status==='nearby_address'?' · Approximate address':''}`;text.append(name,stats);label.append(input,text);row.append(label);
    input.addEventListener('change',()=>{
      if(input.checked)excludedRoofIds.delete(p.id);else excludedRoofIds.add(p.id);
      updatePlan(currentResult.proposal?.panel_model?.model_id || null);
    });
    const reason=result.proposal?.roof_placement_reasons?.find(item=>item.roof_id===p.id);
    if(reason){const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent=reason.excluded?'Why excluded?':reason.panel_count?`${reason.panel_count} panels · Why here?`:'No panels · Why?';const paragraph=document.createElement('p');paragraph.className='hint';paragraph.textContent=reason.reason;details.append(summary,paragraph);row.append(details);}
    $('roof-selection-options').append(row);
  }
}
$('include-all-roofs').addEventListener('click',()=>{
  if(!excludedRoofIds.size)return;
  excludedRoofIds.clear();updatePlan(currentResult.proposal?.panel_model?.model_id || null);
});
$('explain-placement').addEventListener('click',()=>{
  $('methodology').open=true;$('placement-section').open=true;$('placement-section').scrollIntoView({behavior:'smooth',block:'start'});
});
function updatePdfLink() {
  if(!currentJob)return;
  const params=new URLSearchParams();
  for(const id of comparisonIds)params.append('model',id);
  $('pdf-export').href=`/api/report/${encodeURIComponent(currentJob)}${params.size ? '?'+params.toString() : ''}`;
}
function renderPanelComparison(data) {
  const items=data?.recommendations || [];
  $('panel-comparison-section').hidden=items.length<2;
  if(comparisonJob!==currentJob){comparisonIds=[];comparisonJob=currentJob;}
  const known=new Set(items.map(item=>item.model.model_id));
  comparisonIds=comparisonIds.filter((id,index,values)=>known.has(id) && values.indexOf(id)===index);
  if(comparisonIds.length!==2){
    const first=items.find(item=>item.model.model_id===data?.selected_model_id) || items[0];
    comparisonIds=first ? [first.model.model_id,...items.filter(item=>item!==first).slice(0,1).map(item=>item.model.model_id)] : [];
  }
  for(const [index,id] of ['compare-a','compare-b'].entries()){
    const select=$(id);select.replaceChildren();
    for(const item of items){const option=new Option(`${item.rank}. ${panelName(item.model)}`,item.model.model_id);option.disabled=option.value===comparisonIds[1-index];select.append(option);}
    select.value=comparisonIds[index] || '';
  }
  const chosen=comparisonIds.map(id=>items.find(item=>item.model.model_id===id));
  const table=$('panel-comparison-table');table.replaceChildren();$('panel-comparison-reasons').replaceChildren();
  if(chosen.length!==2 || chosen.some(item=>!item)){updatePdfLink();return;}
  const head=document.createElement('thead');const heading=document.createElement('tr');
  for(const title of ['Compare',...chosen.map(item=>panelName(item.model))]){const cell=document.createElement('th');cell.scope='col';cell.textContent=title;heading.append(cell);}head.append(heading);table.append(head);
  const body=document.createElement('tbody');
  const rows=[
    ['Target coverage',item=>item.target_met?'Target met':item.panel_count?'Partial output':'No current fit'],
    ['Budget scope',item=>data.budget_includes_installation===false ? 'Module hardware only; installation extra' : 'Panels + equipment + installation'],
    ['Cost charged to budget',item=>money(item.budget_cost_usd ?? item.estimated_cost_usd)],
    ['Installed estimate',item=>`$${format(item.estimated_cost_usd,2)}`],
    ['Estimated cost range',item=>`$${format(item.estimated_cost_range_usd[0])}–$${format(item.estimated_cost_range_usd[1])}`],
    ['Annual output',item=>`${format(item.estimated_energy_kwh_year)} kWh/year`],
    ['Energy shortfall',item=>`${format(item.energy_shortfall_kwh_year)} kWh/year`],
    ['Panel count',item=>format(item.panel_count)],
    ['Module area used',item=>`${format(item.panel_area_used_m2,1)} m²`],
    ['Power per module',item=>`${format(item.model.rated_power_w)} W`],
    ['Module dimensions',item=>`${format(item.model.width_m,3)} × ${format(item.model.height_m,3)} m`],
    ['Product warranty',item=>item.model.product_warranty_years ? `${format(item.model.product_warranty_years)} years` : 'Unknown'],
    ['Performance warranty',item=>item.model.performance_warranty_years ? `${format(item.model.performance_warranty_years)} years` : 'Unknown']
  ];
  for(const [label,value] of rows){const row=document.createElement('tr');const title=document.createElement('th');title.scope='row';title.textContent=label;row.append(title);for(const item of chosen){const cell=document.createElement('td');cell.textContent=value(item);row.append(cell);}body.append(row);}table.append(body);
  for(const [index,item] of chosen.entries()){
    const card=document.createElement('div');card.className='comparison-option';
    const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=item.model.model_id===data.selected_model_id?`Option ${index?'B':'A'} · currently on map`:`Preview option ${index?'B':'A'} on map`;button.addEventListener('click',()=>previewPanel(item.model.model_id));card.append(button);
    const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent=`When to choose option ${index?'B':'A'}`;const scenario=document.createElement('p');scenario.textContent=panelScenario(item,items);const reasons=document.createElement('ul');for(const reason of item.reasons){const li=document.createElement('li');li.textContent=reason;reasons.append(li);}details.append(summary,scenario,reasons);card.append(details);$('panel-comparison-reasons').append(card);
  }
  updatePdfLink();
}
for(const [index,id] of ['compare-a','compare-b'].entries())$(id).addEventListener('change',()=>{
  comparisonIds[index]=$(id).value;renderPanelComparison(currentResult.panel_recommendations);
});

function renderDashboard(result) {
  const plan=result.proposal, model=plan?.panel_model;
  const items=result.panel_recommendations?.recommendations || [];
  const chosen=items.find(item=>item.model.model_id===model?.model_id);
  const capacity=number(plan?.panel_kw)==null || number(plan?.panel_count)==null ? null : plan.panel_kw*plan.panel_count;
  const energy=number(plan?.estimated_energy_kwh_year), cost=number(plan?.estimated_cost_usd);
  const target=number(plan?.energy_target_kwh_year), budget=number(plan?.budget_usd);
  const coverage=targetCoverage(energy,target);
  const includesInstallation=plan?.budget_includes_installation!==false;
  const budgetCost=number(plan?.budget_cost_usd ?? (includesInstallation ? cost : plan?.estimated_panel_hardware_cost_usd));
  const hardware=number(plan?.estimated_panel_hardware_cost_usd);
  const other=number(plan?.estimated_other_equipment_installation_cost_usd);
  const value=(id,text)=>$(id).textContent=text;
  value('search-summary',`${withUnit(result.request.radius_meters,'m',2)} radius · ${money(budget)} ${includesInstallation?'installed-system':'panels-only'} budget · ${withUnit(target,'kWh / year')} target`);
  value('system-capacity',withUnit(capacity,'kW',2));
  value('capacity-note',number(plan?.panel_count)==null ? 'DC nameplate rating unavailable' : `${format(plan.panel_count)} panels · DC nameplate rating`);
  value('summary-energy',format(energy));value('summary-cost',money(cost));
  value('summary-cost-note',includesInstallation ? 'USD · screening estimate' : 'USD · includes installation beyond panel budget');
  value('summary-coverage',withUnit(coverage,'%',0).replace(' %','%'));
  const targetText=coverage==null ? 'Target coverage unavailable' : energy>=target ? 'Target met under these assumptions' : `Target not met · ${format(Math.max(0,target-energy),2)} kWh / year shortfall`;
  value('summary-target-note',targetText);
  $('summary-coverage').parentElement.classList.toggle('unmet',coverage!=null && energy<target);
  for(const [id,text] of [
    ['selected-power',withUnit(model?.rated_power_w,'W')],['selected-count',format(plan?.panel_count)],
    ['selected-capacity',withUnit(capacity,'kW',2)],['selected-energy',withUnit(energy,'kWh / year')],
    ['selected-unit-cost',money(model?.price_usd)],['selected-hardware-cost',money(hardware)],
    ['selected-other-cost',money(other)],['selected-total-cost',money(cost)],
    ['cost-hardware',money(hardware)],['cost-other',money(other)],['cost-total',money(cost)],['cost-budget',money(budget)],
    ['performance-coverage',withUnit(coverage,'%',0).replace(' %','%')],['performance-energy',withUnit(energy,'kWh / year')],
    ['performance-target',withUnit(target,'kWh / year')],['performance-area',withUnit(plan?.panel_area_used_m2,'m²',1)],
    ['performance-yield',withUnit(capacity>0 && energy!=null ? energy/capacity : null,'kWh / kW / year')]
  ])value(id,text);
  value('selected-rationale',chosen?.reasons?.slice(0,2).join(' ') || 'Update recommendations to see how this configuration compares against your budget, usable roof area, and energy need.');
  const balance=budget==null || budgetCost==null ? null : budget-budgetCost;
  value('cost-budget-label',includesInstallation ? 'Installed-system budget' : 'Module-hardware budget');
  value('cost-balance-label',`${includesInstallation?'':'Panel budget · '}${balance!=null && balance<0 ? 'Budget exceeded' : 'Budget remaining'}`);
  value('cost-balance',money(balance==null ? null : Math.abs(balance)));
  const range=plan?.estimated_cost_range_usd;
  value('cost-range',range?.length===2 && range.every(value=>number(value)!=null) ? `Estimated installed range: ${money(range[0])}–${money(range[1])}. Central estimate shown above.` : 'Cost range unavailable.');
  const breakdownAvailable=hardware!=null && other!=null && hardware>=0 && other>=0;
  const breakdownTotal=breakdownAvailable ? hardware+other : 0;
  $('cost-hardware-bar').style.width=breakdownTotal>0 ? `${hardware/breakdownTotal*100}%` : '0%';
  $('cost-other-bar').style.width=breakdownTotal>0 ? `${other/breakdownTotal*100}%` : '0%';
  $('cost-chart').setAttribute('aria-label',breakdownAvailable ? `Module hardware ${money(hardware)}; other equipment and installation ${money(other)}` : 'Cost breakdown unavailable');
  const meter=$('target-meter');
  if(coverage==null){meter.removeAttribute('aria-valuenow');meter.setAttribute('aria-valuetext','Unavailable');}
  else{meter.setAttribute('aria-valuenow',String(Math.max(0,Math.min(100,coverage))));meter.setAttribute('aria-valuetext',`${format(coverage,1)}% of annual energy target; ${targetText}`);}
  $('target-meter-fill').style.width=coverage==null ? '0%' : `${Math.max(0,Math.min(100,coverage))}%`;
  value('target-status',targetText);
  meter.closest('.data-panel').classList.toggle('unmet',coverage!=null && energy<target);
}
function renderCatalogTable(data) {
  $('catalog-rows').replaceChildren();
  const items=data?.recommendations || [];
  const best=items.find(item=>item.recommended) || items[0];
  $('top-recommendation-reason').textContent=best ? `Top-ranked: ${panelName(best.model)}. ${best.reasons.slice(0,2).join(' ')}` : 'Update recommendations to compare catalog models for this saved analysis.';
  for(const item of items){
    const selected=item.model.model_id===data.selected_model_id;
    const row=document.createElement('tr');row.dataset.model=item.model.model_id;row.classList.toggle('selected',selected);
    const values=[format(item.rank),panelName(item.model),withUnit(item.model.rated_power_w,'W'),
      `${format(item.model.width_m,3)} × ${format(item.model.height_m,3)} m`,format(item.panel_count),
      format(item.estimated_energy_kwh_year),money(item.estimated_cost_usd),
      withUnit(targetCoverage(item.estimated_energy_kwh_year,data.energy_target_kwh_year),'%',0).replace(' %','%')];
    for(const [index,text] of values.entries()){
      const cell=document.createElement(index===1?'th':'td');cell.textContent=text;
      if(index===1){cell.scope='row';if(selected){const note=document.createElement('small');note.textContent='Shown on map';cell.append(note);}}
      row.append(cell);
    }
    const action=document.createElement('td'),button=document.createElement('button');button.type='button';button.className='secondary';
    button.textContent=selected?'Selected':'Preview';button.disabled=selected;
    button.setAttribute('aria-label',`${selected?'Selected':'Preview'} ${panelName(item.model)} on the map`);
    button.addEventListener('click',()=>previewPanel(item.model.model_id));action.append(button);row.append(action);$('catalog-rows').append(row);
  }
}
function editSearch() {
  $('search-controls').open=true;
  $('search-controls').scrollIntoView({behavior:'smooth',block:'start'});
  $('address').focus({preventScroll:true});
}
for(const id of ['edit-search','new-search'])$(id).addEventListener('click',editSearch);
$('compare-models').addEventListener('click',()=>{$('panel-recommendation-section').open=true;});
for(const link of document.querySelectorAll('#result-nav a'))link.addEventListener('click',()=>{const target=document.querySelector(link.hash);if(target instanceof HTMLDetailsElement)target.open=true;});

$('view-selected-map').addEventListener('click',()=>{$('map-section').open=true;});

function renderBudgetScope() {
  const included=$('budget-includes-installation').checked;
  $('budget-label').textContent=included ? 'Installation budget (USD)' : 'Panel hardware budget (USD)';
  $('budget-scope-help').textContent=included ? 'Your budget covers panels, other equipment, and estimated installation.' : 'Your budget covers solar panels only. Other equipment and installation are estimated separately and cost extra.';
}
$('budget-includes-installation').addEventListener('change',()=>{renderBudgetScope();if(currentJob)updatePlan(currentResult?.proposal?.panel_model?.model_id || null);});
renderBudgetScope();
