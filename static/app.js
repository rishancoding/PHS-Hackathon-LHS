const $ = id => document.getElementById(id);
const SQ_FT = 0.09290304; // m² per ft²
const number = value => value == null || value === '' || typeof value === 'boolean' || !Number.isFinite(Number(value)) ? null : Number(value);
const format = (value, digits=0) => number(value) == null ? 'Unavailable' : Number(value).toLocaleString(undefined, {maximumFractionDigits:digits});
const dollars = value => number(value) == null ? '—' : `$${format(value)}`;
const money = value => number(value) == null ? 'Unavailable' : Number(value).toLocaleString(undefined, {style:'currency',currency:'USD'});
const withUnit = (value, unit, digits=0) => number(value) == null ? 'Unavailable' : `${format(value,digits)} ${unit}`;
const targetCoverage = (energy,target) => number(energy) == null || number(target) == null || Number(target)<=0 ? null : Number(energy)/Number(target)*100;
let currentResult, currentJob, activeRoof, view, originalView, dragging;
let planUpdateActive=false, pendingPanelModelId=null;
let excludedRoofIds=new Set(), comparisonIds=[], comparisonJob=null;
// No-panel zones: [left, top, right, bottom] in preview pixels. Saved = applied to the current plan.
let savedZones=[], draftZones=[], zonesJob=null, drawMode=false, zoneStart=null, zoneBusy=false;
function busy(value) { zoneBusy=value; $('draw-zone').disabled=value; renderZones(); $('analyze').disabled=value; $('demo-button').disabled=value; $('lookup-addresses').disabled=value; $('replan').disabled=value; $('include-all-roofs').disabled=value; $('budget-includes-installation').disabled=value; document.querySelectorAll('#roof-selection-options input').forEach(input=>input.disabled=value); }
function error(message) { $('error').textContent=message; $('error').hidden=false; toast(''); }
let toastTimer;
// Short status toast: progress messages stay until replaced; results fade out on their own.
function toast(message,sticky=false){
  clearTimeout(toastTimer); $('status').textContent=message; $('status').classList.toggle('show',Boolean(message));
  if(message && !sticky)toastTimer=setTimeout(()=>$('status').classList.remove('show'),3200);
}
function planInputs() {
  return {budget_usd:Number($('budget').value), budget_includes_installation:$('budget-includes-installation').checked, energy_target_kwh_year:Number($('energy-target').value),
    space_limit_m2:$('space-limit').value.trim() ? Number($('space-limit').value)*($('space-unit').value==='ft2' ? SQ_FT : 1) : null, priority:$('panel-priority').value,
    excluded_roof_ids:[...excludedRoofIds], blocked_zones:savedZones, system_derate:Number($('derate').value) || null};
}
const zoneKey=zone=>zone.map(v=>v.toFixed(2)).join(',');
function zonesDirty(){return draftZones.map(zoneKey).join('|')!==savedZones.map(zoneKey).join('|');}
function renderZones() {
  if(!$('blocked-zones'))return;
  const saved=new Set(savedZones.map(zoneKey)), resolution=currentResult?.analysis?.resolution_m || 1;
  $('blocked-zones').replaceChildren(); $('zone-list').replaceChildren();
  for(const [index,zone] of draftZones.entries()){
    const [left,top,right,bottom]=zone, pending=!saved.has(zoneKey(zone));
    const rect=document.createElementNS('http://www.w3.org/2000/svg','rect');
    rect.setAttribute('x',left);rect.setAttribute('y',top);rect.setAttribute('width',right-left);rect.setAttribute('height',bottom-top);
    rect.setAttribute('class',pending ? 'blocked-zone pending' : 'blocked-zone'); $('blocked-zones').append(rect);
    const item=document.createElement('li'); if(pending)item.className='pending';
    const label=document.createElement('span'); label.textContent=`Zone ${index+1} · ${format((right-left)*resolution,1)} × ${format((bottom-top)*resolution,1)} m`;
    const remove=document.createElement('button'); remove.type='button'; remove.className='text-button'; remove.textContent='Remove'; remove.disabled=zoneBusy;
    remove.setAttribute('aria-label',`Remove zone ${index+1}`); remove.addEventListener('click',()=>{draftZones.splice(index,1);renderZones();});
    item.append(label,remove); $('zone-list').append(item);
  }
  const dirty=zonesDirty();
  $('zone-panel').hidden=!drawMode && draftZones.length===0;
  $('apply-zones').disabled=zoneBusy || !dirty || !currentJob; $('clear-zones').disabled=zoneBusy || draftZones.length===0;
  $('zone-roof').disabled=zoneBusy || !activeRoof;
  $('zone-status').textContent=dirty ? 'Changes not applied yet' : '';
}
function setDrawMode(on) {
  drawMode=on && !!view; zoneStart=null; $('zone-draft')?.remove();
  $('draw-zone').setAttribute('aria-pressed',String(drawMode)); $('draw-zone').textContent=drawMode ? 'Drawing… (Esc to stop)' : 'Draw no-panel zone';
  $('roof-map').classList.toggle('drawing',drawMode);
  renderZones();
}
async function analyze(demo=false) {
  window.roofreadyAddress?.close();
  window.roofreadyLoading.start(demo ? 'Synthetic roofs and sunlight data' : $('address').value.trim(),demo);
  $('error').hidden=true; busy(true);
  toast(demo ? 'Preparing the sample area…' : 'Starting your search…',true);
  const newSearchInputs={...planInputs(), excluded_roof_ids:[], blocked_zones:[]};
  const payload = demo ? {demo:true, ...newSearchInputs} : {
    address:$('address').value.trim(), radius:Number($('radius').value), unit:$('unit').value,
    usable_fraction:Number($('usable').value), panel_kw_per_m2:Number($('density').value), reuse_imagery:$('reuse-imagery').checked, api_key:$('api-key').value, ...newSearchInputs
  };
  try {
    const response=await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok) throw new Error(data.error || 'Could not start analysis.');
    $('api-key').value='';
    currentJob=data.job_id;
    await poll(currentJob);
  } catch(e) { error(e.message); }
  finally { busy(false);window.roofreadyLoading.finish(); }
}
// Status checks ride out brief network blips (three retries with backoff) before giving up.
async function jobStatus(job){
  for(let attempt=0;attempt<4;attempt++){
    let response=null;
    try{response=await fetch(`/api/jobs/${job}`);}catch(e){/* network blip: retry below */}
    if(response?.ok)return response.json();
    if(response?.status===404)throw new Error('This analysis is no longer available.');
    if(attempt<3)await new Promise(resolve=>setTimeout(resolve,500*2**attempt));
  }
  throw new Error('Lost contact with the local server. Check that it is still running.');
}
async function poll(job,preserveView=false) {
  while(true) {
    const state=await jobStatus(job); window.roofreadyLoading.update(state.message);
    if(state.status==='failed') throw new Error(state.message);
    if(state.status==='complete') { render(state.result,job,preserveView); toast(''); await loadHistory(); return; }
    await new Promise(resolve=>setTimeout(resolve,400));
  }
}
function pathFor(geometry) {
  const polygons = geometry.type==='Polygon' ? [geometry.coordinates] : geometry.coordinates;
  return polygons.map(polygon=>polygon.map(ring=>'M'+ring.map(point=>point.join(',')).join('L')+'Z').join('')).join('');
}
// The circular search area fills the frame at full view: no zooming out past it or panning off it.
function setView() {
  if(originalView){
    view[2]=Math.min(view[2],originalView[2]); view[3]=Math.min(view[3],originalView[3]);
    view[0]=Math.max(0,Math.min(originalView[2]-view[2],view[0])); view[1]=Math.max(0,Math.min(originalView[3]-view[3],view[1]));
  }
  $('roof-map').setAttribute('viewBox',view.join(' '));
}
function selectRoof(id) {
  $('map-section').open=true;
  activeRoof=id;
  renderZones();
  $('roof-results').open=true;
  document.querySelectorAll('details.roof-accordion').forEach(detail=>{if(detail.dataset.roof===id)detail.open=true;});
  document.querySelectorAll('[data-roof]').forEach(element=>element.classList.toggle('selected',element.dataset.roof===id));
  const roof=currentResult?.roofs.find(roof=>roof.properties.id===id);
  if(roof){$('selected-roof').hidden=false;$('selected-address').textContent=roofLabel(roof.properties);$('selected-roof-info').textContent=roofNote(`${format(roof.properties.area_m2)} m²`,`${format(roof.properties.estimated_energy_kwh_year)} kWh/yr theoretical potential`,addressNote(roof.properties));}
}
const RESOLVED_ADDRESS=new Set(['reverse_geocoded','nearby_address','synthetic']);
// Roofs without a looked-up street address are labeled by rank instead of a status message.
function roofLabel(p){return RESOLVED_ADDRESS.has(p.address_status) || (!p.address_status && p.address && p.address!=='Address unavailable') ? p.address : `Roof ${p.rank}`;}
function addressNote(p){return p.address_status==='synthetic' ? 'Sample address' : p.address_status==='nearby_address' ? 'Approximate address' : '';}
const roofNote=(...parts)=>parts.filter(Boolean).join(' · ');
const rankedRoofLabel=p=>roofLabel(p).startsWith('Roof ') ? roofLabel(p) : `${p.rank}. ${roofLabel(p)}`;
// Roofs are listed only once a lookup has run, so each can be named by its street address.
function renderAddressCallout(result){
  const pending=result.roofs.some(roof=>roof.properties.address_status==='not_looked_up');
  $('address-callout').hidden=!pending || result.roofs.length===0;
  $('roof-list').hidden=pending && result.roofs.length>0;
}
function render(result,job,preserveView=false) {
  currentResult=result; currentJob=job; activeRoof=null;
  // Keep unapplied drawings across other plan updates on the same analysis.
  const keepDrafts=zonesJob===job && zonesDirty();
  savedZones=(result.blocked_zones || []).map(zone=>zone.bounds); zonesJob=job;
  if(!keepDrafts)draftZones=savedZones.map(zone=>[...zone]);
  if(!preserveView)setDrawMode(false);
  $('selected-roof').hidden=true;
  $('empty').hidden=true; $('intro').hidden=true;
  $('result-content').hidden=false; $('result-details').hidden=false;
  if(!preserveView){$('search-controls').open=true;setTab('map');}
  $('budget-includes-installation').checked=result.proposal?.budget_includes_installation!==false;renderBudgetScope();
  renderRoofSelection(result);
  $('source-badge').hidden=!result.demo; $('replan').hidden=false;
  document.querySelector('.workspace').classList.add('analyzed');
  const seconds=result.search_seconds;
  $('search-time').hidden=typeof seconds!=='number';
  if(typeof seconds==='number')$('search-time').textContent=`Search took ${seconds<60 ? `${seconds.toFixed(1)} s` : `${Math.floor(seconds/60)} min ${Math.round(seconds%60)} s`}`;
  $('result-address').textContent=result.demo ? 'Sample neighborhood' : streetCityState(result.request);
  $('result-address').title=result.request.address;
  $('result-meta').textContent=`${format(result.request.radius_meters,2)} m radius · ${result.request.latitude.toFixed(5)}, ${result.request.longitude.toFixed(5)} · ${format(result.analysis.resolution_m,2)} m analysis pixels`;
  $('roof-count').textContent=format(result.summary.candidate_roofs);
  $('total-energy').textContent=format(result.summary.estimated_energy_kwh_year);
  $('coverage').textContent=format(result.analysis.flux_coverage_percent,1)+'%';
  for(const [id,file] of [['csv-export','roofs.csv'],['geojson-export','roofs.geojson'],['json-export','results.json']]) $(''+id).href=`/results/${job}/${file}`;
  const width=result.analysis.raster_width,height=result.analysis.raster_height;
  originalView=[0,0,width,height]; if(!preserveView || !view) view=[...originalView]; setView();
  // Older saved runs used PNG previews.
  const previews=result.preview_files || {aerial:'aerial.png',sunlight:'sunlight.png'};
  for(const [id,file] of [['aerial',previews.aerial],['sunlight',previews.sunlight]]) {
    const image=$(id); image.setAttribute('href',`/results/${job}/${file}`); image.setAttribute('width',width); image.setAttribute('height',height);
  }
  $('center-marker').setAttribute('transform',`translate(${width/2} ${height/2})`);
  if(!preserveView) {$('heat-toggle').checked=false; $('sunlight').setAttribute('visibility','hidden');}
  renderAddressCallout(result);
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
    const title=document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent=roofNote(roofLabel(p),addressNote(p));outline.append(title);
    outline.addEventListener('click',()=>selectRoof(p.id));
    outline.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectRoof(p.id);}});
    $('outlines').append(outline);
    const row=document.createElement('tr'); row.dataset.roof=p.id; row.tabIndex=0;
    const values=[rankedRoofLabel(p),format(p.area_m2,1),format(p.annual_yield_kwh_per_kw,1),format(p.estimated_capacity_kw,1),format(p.estimated_energy_kwh_year),format(p.flux_coverage_percent,1)+'%'];
    values.forEach((value,index)=>{ const cell=document.createElement('td'); cell.textContent=value;
      if(index===0){const detail=document.createElement('small');detail.textContent=roofNote(addressNote(p),p.partial_roof?'Partial roof at boundary':'',p.existing_panel_area_m2?`${format(p.existing_panel_area_m2)} m² existing panels avoided`:'');cell.append(detail);} row.append(cell); });
    row.addEventListener('click',()=>selectRoof(p.id)); row.addEventListener('keydown',event=>{if(event.key==='Enter'){selectRoof(p.id);}});
    $('roof-rows').append(row);
    const detail=document.createElement('details');detail.className='roof-accordion';detail.dataset.roof=p.id;
    const summary=document.createElement('summary');
    const heading=document.createElement('span');heading.textContent=rankedRoofLabel(p);
    const badge=document.createElement('span');badge.className='roof-energy-badge';badge.textContent=`${format(p.estimated_energy_kwh_year)} kWh/yr theoretical potential`;badge.title='Whole usable roof at a generic panel density, separate from your layout';
    summary.append(heading,badge);detail.append(summary);
    const body=document.createElement('div');body.className='roof-detail-body';
    const info=document.createElement('p');info.className='hint';info.textContent=roofNote(addressNote(p),p.partial_roof?'Partial roof at boundary':'',p.existing_panel_area_m2?`${format(p.existing_panel_area_m2)} m² existing panels avoided`:'');
    const metrics=document.createElement('div');metrics.className='roof-detail-metrics';
    for(const [label,value] of [['Footprint',`${format(p.area_m2,1)} m²`],['Assumed usable area',withUnit(number(p.area_m2)==null || number(result.assumptions?.usable_roof_fraction)==null ? null : p.area_m2*result.assumptions.usable_roof_fraction,'m²',1)],['Average yield',`${format(p.annual_yield_kwh_per_kw,1)} kWh/kW/year`],['Potential capacity',`${format(p.estimated_capacity_kw,1)} kW`],['Sunlight coverage',`${format(p.flux_coverage_percent,1)}%`]]){
      const metric=document.createElement('div');const labelNode=document.createElement('span');labelNode.textContent=label;const valueNode=document.createElement('strong');valueNode.textContent=value;metric.append(labelNode,valueNode);metrics.append(metric);
    }
    const planned=(result.proposal?.panels || []).filter(panel=>panel.roof_id===p.id);
    const planNote=document.createElement('p');planNote.textContent=planned.length ? `${planned.length} blue panels plotted on this roof, with ${format(planned.reduce((sum,panel)=>sum+panel.estimated_energy_kwh_year,0))} kWh/year of estimated output.` : 'No proposed panels are plotted on this roof in the current plan.';
    const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='Highlight roof on map';button.addEventListener('click',()=>{setTab('map');selectRoof(p.id);$('roof-map').scrollIntoView({behavior:'smooth',block:'center'});});
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
  const imagery=result.imagery || {}, date=imagery.imageryDate;
  $('imagery-date').textContent=[date ? `${date.year}-${String(date.month).padStart(2,'0')}` : '',
    imagery.imageryQuality && imagery.imageryQuality!=='HIGH' ? `${imagery.imageryQuality.toLowerCase()} quality` : '',
    imagery.reused_from ? 'saved copy' : ''].filter(Boolean).map(text=>` · ${text}`).join('');
  $('imagery-source').textContent=result.demo ? 'Synthetic imagery' : 'Imagery © Google';
}
function renderProposal(proposal) {
  renderPlottedPanelReport(proposal);
  $('proposed-panels').replaceChildren();
  $('panel-map-scale').textContent='';
  $('panel-count').textContent=proposal ? format(proposal.panel_count) : '—';
  $('plan-cost').textContent=proposal ? '$'+format(proposal.estimated_cost_usd,2) : '—';
  $('plan-energy').textContent=proposal ? format(proposal.estimated_energy_kwh_year) : '—';
  $('plan-status').textContent=proposal ? proposal.status+(proposal.energy_shortfall_kwh_year>0 ? ` Shortfall: ${format(proposal.energy_shortfall_kwh_year)} kWh/year.` : '') : 'Set your budget and yearly energy need, then click Update plan on the Search tab.';
  $('plan-status').classList.toggle('target-met',Boolean(proposal?.target_met));
  $('plan-notes').textContent=proposal ? `${proposal.panel_model ? panelName(proposal.panel_model)+' · ' : ''}$${format(proposal.installed_cost_per_watt,2)}/W installed.` : '';
  if(proposal?.installation_estimate){const estimate=proposal.installation_estimate;const range=proposal.estimated_cost_range_usd;$('plan-notes').textContent+=` ${estimate.state || 'Unknown state'} · ${estimate.region} estimate, range $${format(range[0])}–$${format(range[1])}. Screening estimate, not a quote.`;}
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
  $('placement-explanation').textContent=proposal?.placement_explanation || 'Click Update plan on the Search tab to explain this saved layout.';
  if(!proposal){$('plotted-panel-summary').textContent='Generate a panel plan using your budget and annual energy target to see proposed panels and their roof-by-roof breakdown.';return;}
  const groups=new Map();
  for(const panel of proposal.panels){const group=groups.get(panel.roof_id)||{count:0,energy:0};group.count++;group.energy+=panel.estimated_energy_kwh_year;groups.set(panel.roof_id,group);}
  const remaining=proposal.budget_usd-(proposal.budget_cost_usd ?? proposal.estimated_cost_usd);
  $('plotted-panel-summary').textContent=`The image shows ${format(proposal.panel_count)} proposed ${proposal.panel_width_m} × ${proposal.panel_height_m} m panels across ${groups.size} roof region${groups.size===1?'':'s'}. Together, they are estimated to produce ${format(proposal.estimated_energy_kwh_year)} kWh/year for an estimated installed cost of $${format(proposal.estimated_cost_usd)}, leaving $${format(remaining)} of your ${proposal.budget_includes_installation===false ? 'module-hardware' : 'installed-system'} budget.${proposal.budget_includes_installation===false ? ' Other equipment and installation cost extra.' : ''} ${proposal.target_met?'The estimated annual output meets your target.':`The target remains unmet by ${format(proposal.energy_shortfall_kwh_year)} kWh/year.`}${!proposal.panel_count?' No panels fit the current budget and available roof assumptions.':''}`;
  for(const [id,group] of groups){
    const roof=currentResult.roofs.find(roof=>roof.properties.id===id);
    const row=document.createElement('div');row.className='plotted-roof-row';
    const paragraph=document.createElement('p');paragraph.textContent=`${roof?roofLabel(roof.properties):id}: ${format(group.count)} panels · ${format(group.energy)} kWh/year · $${format(group.count*proposal.panel_kw*1000*proposal.installed_cost_per_watt)} estimated cost.`;
    const explanation=proposal.roof_placement_reasons?.find(reason=>reason.roof_id===id);
    if(explanation)paragraph.textContent+=` ${explanation.reason}`;
    const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='View roof';button.addEventListener('click',()=>{setTab('map');selectRoof(id);$('roof-map').scrollIntoView({behavior:'smooth',block:'center'});});row.append(paragraph,button);$('plotted-roofs').append(row);
  }
}
// Heading shows only "street, city, state": no building name, ZIP or country.
function streetCityState(request){
  const parts=(request.search_address || request.address || '').split(',').map(part=>part.trim()).filter(part=>part && part!=='USA');
  const start=parts.findIndex(part=>/^\d/.test(part));
  if(start<0 || parts.length<start+3)return parts.join(', ');
  const state=parts[start+2].split(/\s+/)[0];
  return `${parts[start]}, ${parts[start+1]}, ${state}`;
}
function fullPanelName(model){return `${model.manufacturer} ${model.model_name}`;}
// "RICH SOLAR" → "Rich Solar"; short acronyms like "REC" stay as written.
const brandName=model=>model.manufacturer.split(' ').map(word=>word.length>3 && word===word.toUpperCase() ? word[0]+word.slice(1).toLowerCase() : word).join(' ');
// e.g. "200 W Pro", "100 W Bifacial 12 V": enough to tell catalog models apart.
function shortModel(model){
  const parts=[`${format(model.rated_power_w)} W`], name=model.model_name;
  if(/\bpro\b/i.test(name))parts.push('Pro');
  if(String(model.bifacial).toLowerCase()==='true')parts.push('Bifacial');
  const volts=name.match(/\b(12|24|48)\s?V\b/i); if(volts)parts.push(`${volts[1]} V`);
  return parts.join(' ');
}
function panelName(model){return `${brandName(model)} ${shortModel(model)}`;}
function setPanelLink(link,url) {
  link.hidden=true;link.removeAttribute('href');
  try{const target=new URL(url);if(['https:','http:'].includes(target.protocol)){link.href=target.href;link.hidden=false;}}catch(e){/* Missing catalog product URL. */}
}
function renderRecommendations(data) {
  $('top-panels').replaceChildren();$('top-panels-note').textContent='';$('chosen-panel-art').replaceChildren();
  $('recommendation-excluded').textContent=''; $('recommendation-notes').textContent='';
  $('chosen-panel-link').hidden=true;
  if(!data){
    $('panel-comparison-section').hidden=true;comparisonIds=[];comparisonJob=currentJob;updatePdfLink();
    const model=currentResult?.proposal?.panel_model;
    $('chosen-panel-name').textContent=model ? panelName(model) : 'Your proposed solar layout';
    $('chosen-panel-label').textContent='PANEL PLOTTED ON THE MAP';
    $('chosen-panel-description').textContent='Open the Search tab and click Update plan to compare panels for this saved search.';
    if(model)setPanelLink($('chosen-panel-link'),model.specifications_source_url || model.price_source_url);
    $('recommendation-summary').textContent='Open the Search tab and click Update plan to compare panels.';return;
  }
  $('budget-includes-installation').checked=data.budget_includes_installation!==false;renderBudgetScope();
  $('budget').value=data.budget_usd; $('energy-target').value=data.energy_target_kwh_year;
  $('space-limit').value=data.space_limit_m2==null ? '' : +(data.space_limit_m2/($('space-unit').value==='ft2' ? SQ_FT : 1)).toFixed(1); $('panel-priority').value=data.priority;
  $('derate').value=currentResult?.assumptions?.system_derate ?? .85;
  const best=data.recommendations.find(item=>item.recommended) || data.recommendations[0];
  const chosen=data.recommendations.find(item=>item.model.model_id===data.selected_model_id) || best;
  if(chosen){
    $('chosen-panel-name').textContent=panelName(chosen.model);$('chosen-panel-name').title=fullPanelName(chosen.model);
    $('chosen-panel-art').replaceChildren(panelIllustration(chosen.model,120/Math.max(chosen.model.width_m,chosen.model.height_m)));
    $('chosen-panel-label').textContent=chosen.recommended ? '#1 recommendation' : `Rank ${chosen.rank} of ${data.recommendations.length}`;
    $('chosen-panel-description').textContent=`${format(chosen.model.width_m,2)} × ${format(chosen.model.height_m,2)} m per panel`;
    setPanelLink($('chosen-panel-link'),chosen.model.specifications_source_url || chosen.model.price_source_url);
  }
  const available=best?.available_roof_area_m2 ?? 0;
  $('recommendation-context').textContent=`${data.recommendations.length} models · $${format(data.budget_usd)} ${data.budget_includes_installation===false?'panels-only':'installed'} budget · ${format(data.energy_target_kwh_year)} kWh/year target.`;
  $('recommendation-summary').textContent='';
  renderTopPanels(data);
  $('recommendation-excluded').textContent=data.excluded_models.length ? `${data.excluded_models.length} portable or flexible models not compared.` : '';
  renderPanelComparison(data);
  $('recommendation-notes').textContent='Screening layout from roof-average sunlight; warranties are not proof of lifespan.';
}
async function previewPanel(modelId) {
  if(currentResult?.proposal?.panel_model?.model_id===modelId && !planUpdateActive)return;
  await updatePlan(modelId);
}
async function updatePlan(selectedModelId=null,overrides={}) {
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
  $('panel-map-scale').textContent=item ? `Updating: ${panelName(item.model)}…` : 'Updating panel layout…';
  toast('Updating layout…',true);
  let success=false;
  try {
    const response=await fetch(`/api/plan/${encodeURIComponent(currentJob)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...planInputs(), selected_model_id:selectedModelId, ...overrides})});
    const result=await response.json(); if(!response.ok)throw new Error(result.error || 'Could not update the plan.');
    render(result,currentJob,true);success=true;
    toast(`Showing ${result.proposal.panel_model ? panelName(result.proposal.panel_model) : 'updated layout'}`);
  } catch(e){
    error(e.message);$('budget-includes-installation').checked=currentResult.proposal?.budget_includes_installation!==false;renderBudgetScope();renderRoofSelection(currentResult);renderProposal(currentResult.proposal);
  } finally {
    planUpdateActive=false;busy(false);$('history').disabled=false;$('roof-map').setAttribute('aria-busy','false');
  }
  const next=pendingPanelModelId;pendingPanelModelId=null;
  if(next)await previewPanel(next);
  return success;
}
function zoom(factor,anchor) {
  if(!view) return;
  const nextWidth=Math.max(originalView[2]/16,Math.min(originalView[2],view[2]*factor));
  const nextHeight=nextWidth*originalView[3]/originalView[2];
  const center=anchor || {x:view[0]+view[2]/2,y:view[1]+view[3]/2};
  const ratioX=(center.x-view[0])/view[2],ratioY=(center.y-view[1])/view[3];
  view=[center.x-ratioX*nextWidth,center.y-ratioY*nextHeight,nextWidth,nextHeight]; setView();
}
$('analysis-form').addEventListener('submit',event=>{event.preventDefault();analyze();});
$('demo-button').addEventListener('click',()=>analyze(true));
$('heat-toggle').addEventListener('change',()=> $('sunlight').setAttribute('visibility',$('heat-toggle').checked?'visible':'hidden'));
$('zoom-in').addEventListener('click',()=>zoom(.75));
$('reset-map').addEventListener('click',()=>{if(originalView){view=[...originalView];setView();}});
$('replan').addEventListener('click',()=>updatePlan());
$('lookup-addresses').addEventListener('click',async()=>{
  if(!currentJob)return;busy(true);$('error').hidden=true;
  try{const response=await fetch(`/api/addresses/${encodeURIComponent(currentJob)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:$('api-key').value})});const data=await response.json();if(!response.ok)throw new Error(data.error || 'Could not look up addresses.');$('api-key').value='';await poll(currentJob,true);toast('Roof addresses added');}catch(e){error(e.message);}finally{busy(false);}
});
function mapPoint(event){const point=new DOMPoint(event.clientX,event.clientY);return point.matrixTransform($('roof-map').getScreenCTM().inverse());}
$('roof-map').addEventListener('wheel',event=>{if(!view)return; event.preventDefault(); zoom(Math.exp(Math.max(-.35,Math.min(.35,event.deltaY*.002))),mapPoint(event));},{passive:false});
$('roof-map').addEventListener('dblclick',event=>{if(!view)return;event.preventDefault();zoom(.5,mapPoint(event));});
// Drawing runs in the capture phase so a drag draws a zone instead of panning or selecting a roof.
function clampToImage(point){return {x:Math.max(0,Math.min(originalView[2],point.x)),y:Math.max(0,Math.min(originalView[3],point.y))};}
$('roof-map').addEventListener('pointerdown',event=>{
  if(!drawMode || !view || event.button>0)return;
  event.stopImmediatePropagation(); event.preventDefault();
  zoneStart=clampToImage(mapPoint(event)); $('roof-map').setPointerCapture(event.pointerId);
  const rect=document.createElementNS('http://www.w3.org/2000/svg','rect'); rect.id='zone-draft'; rect.setAttribute('class','blocked-zone pending');
  $('blocked-zones').append(rect);
},{capture:true});
$('roof-map').addEventListener('pointermove',event=>{
  if(!zoneStart)return; event.stopImmediatePropagation();
  const point=clampToImage(mapPoint(event)), rect=$('zone-draft');
  rect.setAttribute('x',Math.min(zoneStart.x,point.x));rect.setAttribute('y',Math.min(zoneStart.y,point.y));
  rect.setAttribute('width',Math.abs(point.x-zoneStart.x));rect.setAttribute('height',Math.abs(point.y-zoneStart.y));
},{capture:true});
function finishZone(event,keep){
  if(!zoneStart)return; event.stopImmediatePropagation();
  const point=clampToImage(mapPoint(event)), start=zoneStart; zoneStart=null; $('zone-draft')?.remove();
  const zone=[Math.min(start.x,point.x),Math.min(start.y,point.y),Math.max(start.x,point.x),Math.max(start.y,point.y)];
  // Ignore accidental clicks: zones must be at least 0.3 m on each side.
  const minimum=.3/(currentResult?.analysis?.resolution_m || 1);
  if(keep && zone[2]-zone[0]>=minimum && zone[3]-zone[1]>=minimum)draftZones.push(zone);
  renderZones();
}
$('roof-map').addEventListener('pointerup',event=>finishZone(event,true),{capture:true});
$('roof-map').addEventListener('pointercancel',event=>finishZone(event,false),{capture:true});
$('roof-map').addEventListener('click',event=>{if(drawMode)event.stopPropagation();},{capture:true});
$('draw-zone').addEventListener('click',()=>setDrawMode(!drawMode));
document.addEventListener('keydown',event=>{if(event.key==='Escape' && drawMode){setDrawMode(false);renderZones();}});
$('clear-zones').addEventListener('click',()=>{draftZones=[];renderZones();});
$('zone-roof').addEventListener('click',()=>{
  const roof=currentResult?.roofs.find(item=>item.properties.id===activeRoof); if(!roof)return;
  const points=(roof.preview_geometry.type==='Polygon' ? [roof.preview_geometry.coordinates] : roof.preview_geometry.coordinates).flat(2);
  const xs=points.map(point=>point[0]), ys=points.map(point=>point[1]);
  draftZones.push([Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)]);renderZones();
});
$('apply-zones').addEventListener('click',async()=>{
  const zones=draftZones.map(zone=>[...zone]); setDrawMode(false);
  if(await updatePlan(null,{blocked_zones:zones}))
    toast(`Re-planned around ${zones.length} zone${zones.length===1?'':'s'}`);
});
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
    const when=value=>value ? new Date(value).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : '';
    for(const run of runs) $('history').append(new Option([run.demo?'Sample area':run.address,run.radius_meters?`${format(run.radius_meters)} m`:'',when(run.completed_at)].filter(Boolean).join(' · '),run.job_id));
    $('history-wrap').hidden=runs.length===0;
  } catch(e) { /* Search remains usable if saved analysis listing is unavailable. */ }
}
$('history').addEventListener('change',async()=>{
  const job=$('history').value; if(!job)return;
  try { const response=await fetch(`/api/jobs/${encodeURIComponent(job)}`); if(!response.ok)throw new Error('Saved analysis is unavailable.');
    const state=await response.json(); render(state.result,job); toast('Saved search opened');
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
    const stats=document.createElement('small');stats.textContent=roofNote(`${format(p.area_m2)} m²`,`${format(p.annual_yield_kwh_per_kw)} kWh/kW/year`,addressNote(p));text.append(name,stats);label.append(input,text);row.append(label);
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
  const chart=$('panel-comparison-chart');chart.querySelectorAll('thead,tbody').forEach(node=>node.remove());$('panel-comparison-reasons').replaceChildren();$('compare-verdict').textContent='';
  if(chosen.length!==2 || chosen.some(item=>!item)){updatePdfLink();return;}
  const target=data.energy_target_kwh_year;
  // Performance-relevant measures only; the last field says which direction wins.
  const measures=[
    ['Annual output',item=>item.estimated_energy_kwh_year,value=>`${format(value)} kWh`,'higher'],
    ['Target covered',item=>targetCoverage(item.estimated_energy_kwh_year,target),value=>`${format(value)}%`,'higher'],
    ['Installation cost',item=>item.estimated_cost_usd,value=>`$${format(value)}`,'lower'],
    ['Cost per yearly kWh',item=>item.estimated_energy_kwh_year>0 ? item.estimated_cost_usd/item.estimated_energy_kwh_year : null,value=>`$${Number(value).toFixed(2)}`,'lower'],
    ['Roof area used',item=>item.panel_area_used_m2,value=>`${format(value,1)} m²`,'lower'],
    ['Panels needed',item=>item.panel_count,value=>format(value),'lower'],
  ];
  const head=document.createElement('thead'), headRow=document.createElement('tr');
  const corner=document.createElement('th');corner.scope='col';corner.textContent='Measure';headRow.append(corner);
  for(const [index,item] of chosen.entries()){
    const cell=document.createElement('th');cell.scope='col';
    const chip=document.createElement('span');chip.className=`compare-chip ${index?'b':'a'}`;chip.textContent=index?'B':'A';
    cell.append(chip,document.createTextNode(panelName(item.model)));cell.title=fullPanelName(item.model);headRow.append(cell);
  }
  head.append(headRow);chart.append(head);
  const body=document.createElement('tbody'), wins=[0,0];
  for(const [label,get,show,better] of measures){
    const values=chosen.map(get), known=values.every(value=>number(value)!=null);
    const winner=!known || values[0]===values[1] ? -1 : (better==='higher') === (values[0]>values[1]) ? 0 : 1;
    if(winner>=0)wins[winner]++;
    const row=document.createElement('tr');
    const name=document.createElement('th');name.scope='row';name.textContent=label;
    name.title=better==='higher'?'Higher is better':'Lower is better';row.append(name);
    for(const [index,value] of values.entries()){
      const cell=document.createElement('td');cell.textContent=number(value)==null ? '—' : show(value);
      if(winner===index){cell.className='wins';const badge=document.createElement('span');badge.className='win-badge';badge.textContent='✓ Better';cell.append(badge);}
      row.append(cell);
    }
    body.append(row);
  }
  chart.append(body);
  const names=chosen.map(item=>panelName(item.model)), leader=wins[0]>wins[1]?0:1;
  $('compare-verdict').textContent=wins[0]===wins[1] ? `Even split: each option leads on ${wins[0]} of ${measures.length} measures.`
    : `${leader?'B':'A'} · ${names[leader]} leads on ${wins[leader]} of ${measures.length} measures.`;
  for(const [index,item] of chosen.entries()){
    const button=document.createElement('button');button.type='button';const onMap=item.model.model_id===data.selected_model_id;
    button.className=onMap?'secondary':'primary';button.disabled=onMap;
    button.textContent=onMap?`${index?'B':'A'} is on the map`:`Preview ${index?'B':'A'} on map`;
    button.addEventListener('click',()=>previewPanel(item.model.model_id));$('panel-comparison-reasons').append(button);
  }
  updatePdfLink();
}
for(const [index,id] of ['compare-a','compare-b'].entries())$(id).addEventListener('change',()=>{
  comparisonIds[index]=$(id).value;renderPanelComparison(currentResult.panel_recommendations);
});

function renderDashboard(result) {
  const impact=result.impact;
  $('impact-savings').textContent=impact ? `$${format(impact.annual_bill_savings_usd)}/yr` : '—';
  $('impact-price').textContent=impact ? `At ${format(impact.electricity_price_cents_per_kwh,1)}¢/kWh (${impact.price_region})` : '';
  $('impact-payback').textContent=impact?.simple_payback_years ? `${format(impact.simple_payback_years,1)} years` : '—';
  $('impact-co2').textContent=impact ? format(impact.co2_avoided_tonnes_per_year,1) : '—';
  $('impact-homes').textContent=impact ? format(impact.homes_powered,1) : '—';
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
  $('search-summary').replaceChildren(...[`${format(result.request.radius_meters)} m radius`,`$${format(budget)} ${includesInstallation?'budget':'panel budget'}`,`${format(target)} kWh/yr target`]
    .map(text=>{const chip=document.createElement('li');chip.textContent=text;return chip;}));
  value('system-capacity',withUnit(capacity,'kW',2));
  value('capacity-note',number(plan?.panel_count)==null ? '' : `${format(plan.panel_count)} panels`);
  value('summary-energy',format(energy));value('summary-cost',cost==null ? '—' : `$${format(cost)}`);
  value('summary-cost-note',includesInstallation ? '' : 'Installation is extra to your panel budget');
  value('summary-coverage',withUnit(coverage,'%',0).replace(' %','%'));
  const targetText=coverage==null ? '' : energy>=target ? 'Target met' : `${format(Math.max(0,target-energy))} kWh short`;
  $('summary-meter-fill').style.width=`${Math.min(100,coverage ?? 0)}%`;
  value('summary-target-note',targetText);
  $('summary-coverage').parentElement.classList.toggle('unmet',coverage!=null && energy<target);
  for(const [id,text] of [
    ['selected-power',withUnit(model?.rated_power_w,'W')],['selected-count',format(plan?.panel_count)],
    ['selected-energy',withUnit(energy,'kWh')],['selected-total-cost',cost==null ? '—' : `$${format(cost)}`],
    ['cost-hardware',dollars(hardware)],['cost-other',dollars(other)],['cost-total',dollars(cost)],['cost-budget',dollars(budget)],
    ['performance-energy',withUnit(energy,'kWh')],
    ['performance-target',withUnit(target,'kWh')],['performance-area',withUnit(plan?.panel_area_used_m2,'m²',1)],
    ['performance-yield',withUnit(capacity>0 && energy!=null ? energy/capacity : null,'kWh/kW')]
  ])value(id,text);
  const balance=budget==null || budgetCost==null ? null : budget-budgetCost;
  value('cost-budget-label',includesInstallation ? 'Budget' : 'Panel budget');
  value('cost-balance-label',balance!=null && balance<0 ? 'Over budget' : 'Remaining');
  value('cost-balance',dollars(balance==null ? null : Math.abs(balance)));
  const range=plan?.estimated_cost_range_usd;
  value('cost-range',range?.length===2 && range.every(value=>number(value)!=null) ? `Likely range $${format(range[0])}–$${format(range[1])}` : '');
  const breakdownAvailable=hardware!=null && other!=null && hardware>=0 && other>=0;
  const breakdownTotal=breakdownAvailable ? hardware+other : 0;
  $('cost-hardware-bar').style.width=breakdownTotal>0 ? `${hardware/breakdownTotal*100}%` : '0%';
  $('cost-other-bar').style.width=breakdownTotal>0 ? `${other/breakdownTotal*100}%` : '0%';
  $('cost-chart').setAttribute('aria-label',breakdownAvailable ? `Module hardware ${money(hardware)}; other equipment and installation ${money(other)}` : 'Cost breakdown unavailable');
}
function renderCatalogTable(data) {
  const items=data?.recommendations || [];
  const best=items.find(item=>item.recommended) || items[0];
  $('top-recommendation-reason').textContent=best ? '' : 'Open the Search tab and click Update plan to compare panels.';
}
const SVG='http://www.w3.org/2000/svg';
function svgNode(tag,attributes={}){const node=document.createElementNS(SVG,tag);for(const [key,value] of Object.entries(attributes))node.setAttribute(key,value);return node;}
// Illustration drawn from catalog data (proportions, frame color, bifacial); not a product photo.
let illustrationCount=0;
function panelIllustration(model,scale) {
  const width=model.width_m*scale, height=model.height_m*scale, box=150, x=(box-width)/2, y=(box-height)/2;
  const silver=/silver/i.test(model.model_name), bifacial=String(model.bifacial).toLowerCase()==='true';
  const columns=Math.max(2,Math.round(model.width_m/.16)), rows=Math.max(2,Math.round(model.height_m/.16));
  const frame=Math.max(1.5,Math.min(width,height)*.035), gap=bifacial ? .9 : .45;
  const svg=svgNode('svg',{viewBox:`0 0 ${box} ${box}`,class:'panel-art',role:'img','aria-label':`Illustration of ${panelName(model)}, ${format(model.width_m,2)} by ${format(model.height_m,2)} meters`});
  // Unique per drawing: the same model can appear on several tabs, and a gradient inside a hidden tab won't paint.
  const id=`cells-${model.model_id.replace(/[^a-z0-9]/gi,'')}-${++illustrationCount}`;
  const defs=svgNode('defs'), gradient=svgNode('linearGradient',{id,x1:0,y1:0,x2:1,y2:1});
  gradient.append(svgNode('stop',{offset:0,'stop-color':'#2e4a72'}),svgNode('stop',{offset:.55,'stop-color':'#1d3352'}),svgNode('stop',{offset:1,'stop-color':'#15253d'}));
  defs.append(gradient); svg.append(defs);
  svg.append(svgNode('rect',{x:x+3,y:y+4,width,height,rx:2,class:'panel-art-shadow'}));
  svg.append(svgNode('rect',{x,y,width,height,rx:2,class:silver?'panel-art-frame silver':'panel-art-frame'}));
  const innerX=x+frame, innerY=y+frame, innerWidth=width-2*frame, innerHeight=height-2*frame;
  svg.append(svgNode('rect',{x:innerX,y:innerY,width:innerWidth,height:innerHeight,class:bifacial?'panel-art-glass bifacial':'panel-art-glass'}));
  const cellWidth=(innerWidth-gap*(columns+1))/columns, cellHeight=(innerHeight-gap*(rows+1))/rows;
  const cells=svgNode('g',{fill:`url(#${id})`});
  for(let row=0;row<rows;row++)for(let column=0;column<columns;column++)
    cells.append(svgNode('rect',{x:innerX+gap+column*(cellWidth+gap),y:innerY+gap+row*(cellHeight+gap),width:Math.max(.5,cellWidth),height:Math.max(.5,cellHeight),rx:.4}));
  svg.append(cells);
  svg.append(svgNode('path',{d:`M${innerX} ${innerY+innerHeight*.35} L${innerX+innerWidth*.45} ${innerY} L${innerX+innerWidth*.7} ${innerY} L${innerX} ${innerY+innerHeight*.62}Z`,class:'panel-art-glare'}));
  return svg;
}
// Highlights: measures where a model places in the top 3 of all compared models.
const ADVANTAGES=[
  [item=>item.estimated_energy_kwh_year,'higher',['Most output','2nd-most output','3rd-most output'],null],
  [item=>item.estimated_energy_kwh_year>0 ? item.estimated_cost_usd/item.estimated_energy_kwh_year : null,'lower',['Lowest cost per kWh','2nd-lowest cost per kWh','3rd-lowest cost per kWh'],value=>`$${value.toFixed(2)} per yearly kWh`],
  [item=>item.model.price_per_watt_usd,'lower',['Cheapest per watt','2nd-cheapest per watt','3rd-cheapest per watt'],null],
  [item=>item.model.power_density_w_per_m2,'higher',['Most power per m²','2nd-most power per m²','3rd-most power per m²'],null],
  [item=>item.panel_count>0 ? item.panel_count : null,'lower',['Fewest panels','2nd-fewest panels','3rd-fewest panels'],null],
];
function panelAdvantages(item,items) {
  const facts=[];
  for(const [get,better,labels,show] of ADVANTAGES){
    const value=get(item); if(number(value)==null)continue;
    const place=items.map(get).filter(other=>number(other)!=null && (better==='higher' ? other>value : other<value)).length;
    if(place<3)facts.push({place,text:labels[place],detail:show ? show(value) : ''});
  }
  const highlights=facts.sort((a,b)=>a.place-b.place).slice(0,2);
  const {product_warranty_years:product,performance_warranty_years:output}=item.model;
  const warranty=[product ? `${format(product)}-yr product` : '',output ? `${format(output)}-yr output` : ''].filter(Boolean).join(' · ');
  return {highlights,warranty:warranty ? `${warranty} warranty` : 'Warranty not documented'};
}
function renderTopPanels(data) {
  const items=data.recommendations, container=$('top-panels'), previousScroll=container.scrollTop;
  container.replaceChildren();
  const scale=120/Math.max(...items.map(item=>Math.max(item.model.width_m,item.model.height_m)));
  const places=['Best match','Runner-up','Third choice'];
  let selectedCard=null;
  for(const [index,item] of items.entries()){
    const model=item.model, selected=model.model_id===data.selected_model_id;
    const card=document.createElement('article');card.className=`top-panel${selected?' selected':''}`;card.setAttribute('role','listitem');card.dataset.model=model.model_id;
    card.style.setProperty('--delay',`${Math.min(index,4)*60}ms`);
    card.addEventListener('click',event=>{const control=event.target.closest('a,button');if(control && card.contains(control))return; if(!selected)previewPanel(model.model_id);});
    const art=document.createElement('div');art.className='top-panel-art';art.append(panelIllustration(model,scale));
    const rank=document.createElement('span');rank.className='top-panel-rank';rank.textContent=`#${item.rank}`;
    const place=document.createElement('span');place.className='top-panel-place';place.textContent=[places[index],selected?'On map':''].filter(Boolean).join(' · ');
    const size=document.createElement('span');size.className='top-panel-size';size.textContent=`${format(model.width_m,2)} × ${format(model.height_m,2)} m · ${format(model.power_density_w_per_m2)} W/m²`;
    art.append(rank,place,size);
    if(String(model.bifacial).toLowerCase()==='true'){const tag=document.createElement('span');tag.className='top-panel-tag';tag.textContent='Bifacial';tag.title='Collects light on both sides; rear-side gain is not counted';art.append(tag);}
    const head=document.createElement('div');head.className='top-panel-head';
    const brand=document.createElement('p');brand.className='eyebrow';brand.textContent=brandName(model);
    const name=document.createElement('h3');name.textContent=shortModel(model);name.title=model.model_name;
    head.append(brand,name);
    const stats=document.createElement('dl');stats.className='top-panel-stats';
    for(const [label,value] of [['Yearly output',`${format(item.estimated_energy_kwh_year)} kWh`],['Installation',`$${format(item.estimated_cost_usd)}`],['Panels',format(item.panel_count)],['Hardware',`$${Number(model.price_per_watt_usd).toFixed(2)}/W`]]){
      const cell=document.createElement('div');const term=document.createElement('dt');term.textContent=label;const detail=document.createElement('dd');detail.textContent=value;cell.append(term,detail);stats.append(cell);
    }
    const coverage=Math.max(0,targetCoverage(item.estimated_energy_kwh_year,data.energy_target_kwh_year) ?? 0);
    const meter=document.createElement('div');meter.className='top-panel-meter';
    const meterLabel=document.createElement('span');meterLabel.textContent=item.target_met ? 'Meets target' : `${format(coverage)}% of target`;
    const track=document.createElement('div');track.className='top-panel-track';track.setAttribute('role','meter');track.setAttribute('aria-valuemin','0');track.setAttribute('aria-valuemax','100');track.setAttribute('aria-valuenow',String(Math.round(Math.min(100,coverage))));track.setAttribute('aria-label','Energy target covered');
    const fill=document.createElement('span');fill.style.width=`${Math.min(100,coverage)}%`;fill.className=item.target_met?'met':'';track.append(fill);meter.append(meterLabel,track);
    const {highlights,warranty}=panelAdvantages(item,items);
    const blurb=document.createElement('ul');blurb.className='top-panel-chips';blurb.setAttribute('aria-label','Highlights');
    for(const {text,detail,place:rank} of highlights){const chip=document.createElement('li');chip.textContent=text;if(detail)chip.title=detail;if(rank===0)chip.className='best';blurb.append(chip);}
    const actions=document.createElement('div');actions.className='top-panel-actions';
    const warrantyNote=document.createElement('p');warrantyNote.className='top-panel-warranty';warrantyNote.textContent=warranty;
    const buttons=document.createElement('div');buttons.className='top-panel-buttons';
    const preview=document.createElement('button');preview.type='button';preview.className=selected?'secondary':'primary';preview.textContent=selected?'On map':'Preview';preview.disabled=selected;
    preview.setAttribute('aria-label',`${selected?'Shown on map':'Preview on map'}: ${panelName(model)}`);preview.addEventListener('click',()=>previewPanel(model.model_id));
    const specs=document.createElement('a');specs.className='text-link';specs.textContent='Specs ↗';specs.target='_blank';specs.rel='noopener noreferrer';setPanelLink(specs,model.specifications_source_url || model.price_source_url);
    buttons.append(preview,specs);actions.append(warrantyNote,buttons);
    card.append(art,head,stats,meter,blurb,actions);container.append(card);
    if(selected)selectedCard=card;
  }
  // Keep the reader's place after a re-plan, and bring the plotted model into view without moving the page.
  container.scrollTop=previousScroll;
  if(selectedCard){
    const top=selectedCard.offsetTop, bottom=top+selectedCard.offsetHeight;
    if(top<container.scrollTop || bottom>container.scrollTop+container.clientHeight)container.scrollTo({top:top-8,behavior:'smooth'});
  }
  $('top-panels-note').textContent=`Scroll for all ${items.length} models · drawings to scale`;
}
// Results are split into tabs; the address header and summary tiles stay above them.
const TABS=['map','panels','details','roofs','search'];
for(const name of TABS){
  const panels=[...document.querySelectorAll(`[data-tab="${name}"]`)], tab=document.querySelector(`[data-tab-target="${name}"]`);
  tab.id=`tab-${name}`;
  panels.forEach((panel,index)=>{panel.id ||= `tabpanel-${name}-${index}`;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',tab.id);});
  tab.setAttribute('aria-controls',panels.map(panel=>panel.id).join(' '));
}
function setTab(name){
  if(!TABS.includes(name))return;
  document.querySelector('.workspace').dataset.view=name;
  for(const tab of document.querySelectorAll('#result-tabs [role="tab"]')){
    const active=tab.dataset.tabTarget===name; tab.setAttribute('aria-selected',String(active)); tab.tabIndex=active?0:-1;
  }
  if(name==='search')$('address').focus({preventScroll:true});
}
for(const tab of document.querySelectorAll('#result-tabs [role="tab"]')){
  tab.addEventListener('click',()=>setTab(tab.dataset.tabTarget));
  tab.addEventListener('keydown',event=>{
    if(!['ArrowRight','ArrowLeft','Home','End'].includes(event.key))return; event.preventDefault();
    const index=TABS.indexOf(tab.dataset.tabTarget);
    const next=event.key==='Home'?0:event.key==='End'?TABS.length-1:(index+(event.key==='ArrowRight'?1:-1)+TABS.length)%TABS.length;
    setTab(TABS[next]); document.querySelector(`#result-tabs [data-tab-target="${TABS[next]}"]`).focus();
  });
}
$('compare-models').addEventListener('click',event=>{event.preventDefault();setTab('panels');$('panel-comparison-section').open=true;$('result-tabs').scrollIntoView({behavior:'smooth',block:'start'});});


function renderBudgetScope() {
  const included=$('budget-includes-installation').checked;
  $('budget-label').textContent=included ? 'Budget ($)' : 'Panel budget ($)';
  $('budget-scope-help').textContent=included ? 'Budget covers panels and installation.' : 'Budget covers panels only; installation is extra.';
}
$('budget-includes-installation').addEventListener('change',()=>{renderBudgetScope();if(currentJob)updatePlan(currentResult?.proposal?.panel_model?.model_id || null);});
renderBudgetScope();
