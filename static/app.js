const $ = id => document.getElementById(id);
const format = (value, digits=0) => value == null ? 'Unavailable' : Number(value).toLocaleString(undefined, {maximumFractionDigits:digits});
let currentResult, currentJob, activeRoof, view, originalView, dragging;
function busy(value) { $('analyze').disabled=value; $('demo-button').disabled=value; $('lookup-addresses').disabled=value; $('replan').disabled=value; }
function error(message) { $('error').textContent=message; $('error').hidden=false; }
function planInputs() {
  return {budget_usd:Number($('budget').value), energy_target_kwh_year:Number($('energy-target').value), installed_cost_per_watt:Number($('cost-per-watt').value)};
}
async function analyze(demo=false) {
  $('error').hidden=true; busy(true);
  $('status').textContent=demo ? 'Building a synthetic demo…' : 'Starting your analysis…';
  const payload = demo ? {demo:true, ...planInputs()} : {
    address:$('address').value.trim(), radius:Number($('radius').value), unit:$('unit').value,
    usable_fraction:Number($('usable').value), panel_kw_per_m2:Number($('density').value), api_key:$('api-key').value, ...planInputs()
  };
  try {
    const response=await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok) throw new Error(data.error || 'Could not start analysis.');
    $('api-key').value='';
    currentJob=data.job_id;
    await poll(currentJob);
  } catch(e) { error(e.message); $('status').textContent='Analysis could not finish.'; }
  finally { busy(false); }
}
async function poll(job) {
  while(true) {
    const response=await fetch(`/api/jobs/${job}`);
    if(!response.ok) throw new Error('Could not read analysis status.');
    const state=await response.json(); $('status').textContent=state.message;
    if(state.status==='failed') throw new Error(state.message);
    if(state.status==='complete') { render(state.result,job); await loadHistory(); return; }
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
}
function pathFor(geometry) {
  const polygons = geometry.type==='Polygon' ? [geometry.coordinates] : geometry.coordinates;
  return polygons.map(polygon=>polygon.map(ring=>'M'+ring.map(point=>point.join(',')).join('L')+'Z').join('')).join('');
}
function setView() { $('roof-map').setAttribute('viewBox',view.join(' ')); }
function selectRoof(id) {
  activeRoof=id;
  $('roof-results').open=true;
  document.querySelectorAll('details.roof-accordion').forEach(detail=>{if(detail.dataset.roof===id)detail.open=true;});
  document.querySelectorAll('[data-roof]').forEach(element=>element.classList.toggle('selected',element.dataset.roof===id));
  const row=document.querySelector(`tr[data-roof="${id}"]`);
  if(row) row.scrollIntoView({behavior:'smooth',block:'nearest'});
  const roof=currentResult?.roofs.find(roof=>roof.properties.id===id);
  if(roof){$('selected-roof').hidden=false;$('selected-address').textContent=roofLabel(roof.properties);$('selected-roof-info').textContent=`${roof.properties.id} · ${format(roof.properties.area_m2,1)} m² footprint · ${format(roof.properties.estimated_energy_kwh_year)} kWh/year estimated potential. ${addressNote(roof.properties)}`;}
}
function roofLabel(p){return p.address || 'Address unavailable';}
function addressNote(p){return p.address_status==='synthetic' ? 'Synthetic demo address.' : p.address_status==='nearby_address' ? 'Nearby address; roof match is approximate.' : p.address_status==='reverse_geocoded' ? 'Reverse-geocoded address; verify the building match.' : 'Address not resolved. Use Look up roof addresses.';}
function render(result,job,preserveView=false) {
  currentResult=result; currentJob=job; activeRoof=null;
  $('selected-roof').hidden=true;
  $('empty').hidden=true; $('result-content').hidden=false;
  $('source-badge').textContent=result.demo ? 'SYNTHETIC DEMO · NOT REAL SOLAR DATA' : 'GOOGLE SOLAR DATA · SCREENING ESTIMATES';
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
  for(const roof of result.roofs) {
    const p=roof.properties;
    const outline=document.createElementNS('http://www.w3.org/2000/svg','path');
    outline.setAttribute('d',pathFor(roof.preview_geometry)); outline.setAttribute('fill-rule','evenodd');
    outline.setAttribute('class','roof-outline'); outline.dataset.roof=p.id;
    outline.setAttribute('tabindex','0'); outline.setAttribute('role','button'); outline.setAttribute('aria-label',`${roofLabel(p)}, ${format(p.estimated_energy_kwh_year)} kWh per year`);
    const title=document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent=`${roofLabel(p)} · ${addressNote(p)}`;outline.append(title);
    outline.addEventListener('click',()=>selectRoof(p.id));
    outline.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectRoof(p.id);}});
    $('outlines').append(outline);
    const row=document.createElement('tr'); row.dataset.roof=p.id; row.tabIndex=0;
    const values=[`${p.rank}. ${roofLabel(p)}`,format(p.area_m2,1),format(p.annual_yield_kwh_per_kw,1),format(p.estimated_capacity_kw,1),format(p.estimated_energy_kwh_year),format(p.flux_coverage_percent,1)+'%'];
    values.forEach((value,index)=>{ const cell=document.createElement('td'); cell.textContent=value;
      if(index===0){const detail=document.createElement('small');detail.textContent=`${p.id} · ${addressNote(p)}${p.partial_roof?' Partial roof at boundary.':''}`;cell.append(detail);} row.append(cell); });
    row.addEventListener('click',()=>selectRoof(p.id)); row.addEventListener('keydown',event=>{if(event.key==='Enter'){selectRoof(p.id);}});
    $('roof-rows').append(row);
    const detail=document.createElement('details');detail.className='roof-accordion';detail.dataset.roof=p.id;
    const summary=document.createElement('summary');
    const heading=document.createElement('span');heading.textContent=`${p.rank}. ${roofLabel(p)}`;
    const badge=document.createElement('span');badge.className='roof-energy-badge';badge.textContent=`${format(p.estimated_energy_kwh_year)} kWh/year potential`;
    summary.append(heading,badge);detail.append(summary);
    const body=document.createElement('div');body.className='roof-detail-body';
    const info=document.createElement('p');info.className='hint';info.textContent=`${p.id} · ${addressNote(p)}${p.partial_roof?' Partial roof at boundary.':''}`;
    const metrics=document.createElement('div');metrics.className='roof-detail-metrics';
    for(const [label,value] of [['Footprint',`${format(p.area_m2,1)} m²`],['Average yield',`${format(p.annual_yield_kwh_per_kw,1)} kWh/kW/year`],['Potential capacity',`${format(p.estimated_capacity_kw,1)} kW`],['Sunlight coverage',`${format(p.flux_coverage_percent,1)}%`]]){
      const metric=document.createElement('div');const labelNode=document.createElement('span');labelNode.textContent=label;const valueNode=document.createElement('strong');valueNode.textContent=value;metric.append(labelNode,valueNode);metrics.append(metric);
    }
    const planned=(result.proposal?.panels || []).filter(panel=>panel.roof_id===p.id);
    const planNote=document.createElement('p');planNote.textContent=planned.length ? `${planned.length} blue panels plotted on this roof, with ${format(planned.reduce((sum,panel)=>sum+panel.estimated_energy_kwh_year,0))} kWh/year of estimated output.` : 'No proposed panels are plotted on this roof in the current plan.';
    const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='Highlight roof on map';button.addEventListener('click',()=>{selectRoof(p.id);$('roof-map').scrollIntoView({behavior:'smooth',block:'center'});});
    body.append(info,metrics,planNote,button);detail.append(body);$('roof-accordions').append(detail);
  }
  $('no-roofs').hidden=result.roofs.length>0;
  renderProposal(result.proposal);
  $('findings').textContent=result.report?.findings || 'Update the panel plan to generate a summary for this saved search.';
  $('next-steps').textContent=result.report?.next_steps || 'Verify roof addresses and usable area with a qualified solar installer before proceeding.';
  const a=result.assumptions;
  $('assumptions').textContent=`Assumptions: ${format(a.usable_roof_fraction*100)}% usable footprint · ${a.panel_kw_per_m2} kW/m² panel capacity · ${a.minimum_roof_area_m2} m² minimum candidate area. Ranked by estimated annual energy.`;
  const date=result.imagery?.imageryDate;
  $('imagery-date').textContent=date ? `· ${date.year}-${date.month}-${date.day}` : '';
  document.querySelector('.map-footer span:last-child').firstChild.textContent=result.demo ? 'Synthetic imagery ' : 'Imagery: Google Solar API ';
}
function renderProposal(proposal) {
  renderPlottedPanelReport(proposal);
  $('proposed-panels').replaceChildren();
  $('panel-count').textContent=proposal ? format(proposal.panel_count) : '—';
  $('plan-cost').textContent=proposal ? '$'+format(proposal.estimated_cost_usd,2) : '—';
  $('plan-energy').textContent=proposal ? format(proposal.estimated_energy_kwh_year) : '—';
  $('plan-status').textContent=proposal ? proposal.status+(proposal.energy_shortfall_kwh_year>0 ? ` Shortfall: ${format(proposal.energy_shortfall_kwh_year)} kWh/year.` : '') : 'Set your budget and annual energy target, then click Update budget / target to create a plan from this saved analysis.';
  $('plan-status').classList.toggle('target-met',Boolean(proposal?.target_met));
  $('plan-notes').textContent=proposal ? `${proposal.panel_width_m} × ${proposal.panel_height_m} m panels · ${format(proposal.panel_kw,2)} kW each · assumed $${proposal.installed_cost_per_watt}/W. ${proposal.notes}` : '';
  if(!proposal)return;
  for(const panel of proposal.panels) {
    const outline=document.createElementNS('http://www.w3.org/2000/svg','path');
    outline.setAttribute('d',pathFor(panel.preview_geometry)); outline.setAttribute('class','proposed-panel');
    const title=document.createElementNS('http://www.w3.org/2000/svg','title');
    const roof=currentResult.roofs.find(roof=>roof.properties.id===panel.roof_id);
    title.textContent=`Proposed panel at ${roof ? roofLabel(roof.properties) : panel.roof_id} · ${format(panel.estimated_energy_kwh_year)} kWh/year`;
    outline.append(title); outline.addEventListener('click',()=>selectRoof(panel.roof_id)); $('proposed-panels').append(outline);
  }
}
function renderPlottedPanelReport(proposal) {
  $('plotted-roofs').replaceChildren();
  if(!proposal){$('plotted-panel-summary').textContent='Generate a panel plan using your budget and annual energy target to see proposed panels and their roof-by-roof breakdown.';return;}
  const groups=new Map();
  for(const panel of proposal.panels){const group=groups.get(panel.roof_id)||{count:0,energy:0};group.count++;group.energy+=panel.estimated_energy_kwh_year;groups.set(panel.roof_id,group);}
  const remaining=Math.max(0,proposal.budget_usd-proposal.estimated_cost_usd);
  $('plotted-panel-summary').textContent=`The image shows ${format(proposal.panel_count)} proposed ${proposal.panel_width_m} × ${proposal.panel_height_m} m panels across ${groups.size} roof region${groups.size===1?'':'s'}. Together, they are estimated to produce ${format(proposal.estimated_energy_kwh_year)} kWh/year for an assumed installed cost of $${format(proposal.estimated_cost_usd)}, leaving $${format(remaining)} of your budget. ${proposal.target_met?'The estimated annual output meets your target.':`The target remains unmet by ${format(proposal.energy_shortfall_kwh_year)} kWh/year.`}${!proposal.panel_count?' No panels fit the current budget and available roof assumptions.':''}`;
  for(const [id,group] of groups){
    const roof=currentResult.roofs.find(roof=>roof.properties.id===id);
    const row=document.createElement('div');row.className='plotted-roof-row';
    const paragraph=document.createElement('p');paragraph.textContent=`${roof?roofLabel(roof.properties):id} (${id}): ${format(group.count)} panels · ${format(group.energy)} kWh/year · $${format(group.count*proposal.panel_kw*1000*proposal.installed_cost_per_watt)} estimated cost.`;
    const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='View roof';button.addEventListener('click',()=>{selectRoof(id);$('roof-map').scrollIntoView({behavior:'smooth',block:'center'});});row.append(paragraph,button);$('plotted-roofs').append(row);
  }
}
async function updatePlan() {
  if(!currentJob)return;
  $('error').hidden=true; $('replan').disabled=true;
  try {
    const response=await fetch(`/api/plan/${encodeURIComponent(currentJob)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(planInputs())});
    const result=await response.json(); if(!response.ok)throw new Error(result.error || 'Could not update the plan.');
    render(result,currentJob,true); $('status').textContent='Panel plan updated. No new Google API requests.';
  } catch(e){error(e.message);} finally {$('replan').disabled=false;}
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
$('replan').addEventListener('click',updatePlan);
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
    const runs=await response.json(); $('history').replaceChildren(new Option('Choose an analysis',''));
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
