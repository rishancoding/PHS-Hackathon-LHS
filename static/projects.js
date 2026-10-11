/* Named Atlas snapshots, separate from this device's recent analyses. */
(() => {
  let connected=false, requestBusy=false, appBusy=false, activeJob=null;
  const status=message=>{ $('projects-status').textContent=message; };
  function controls(){
    const blocked=requestBusy || appBusy;
    $('project-save').disabled=blocked || !connected || !currentResult || !currentJob;
    $('project-name').disabled=blocked;
    $('projects-refresh').disabled=blocked;
    $('save-project-shortcut').disabled=blocked;
    $('project-list').querySelectorAll('button').forEach(button=>button.disabled=blocked);
  }
  async function request(path,options={}){
    const response=await fetch(path,options);
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error || 'Saved project request failed. Try again.');
    return data;
  }
  function listProjects(projects){
    $('project-list').replaceChildren();
    for(const project of projects){
      const row=document.createElement('li');row.className='project-card';row.dataset.project=project.id;
      const title=document.createElement('h3');title.textContent=project.name;
      const address=document.createElement('p');address.className='hint';address.textContent=project.demo ? 'Sample area · synthetic data' : project.address;
      const model=document.createElement('p');model.className='hint';model.textContent=project.panel_model || 'No panel selected';
      const stats=document.createElement('p');stats.className='project-stats';stats.textContent=`${dollars(project.estimated_cost_usd)} installed · ${format(project.estimated_energy_kwh_year)} kWh/year · ${dollars(project.annual_bill_savings_usd)}/year savings`;
      const footer=document.createElement('div');footer.className='project-card-footer';
      const date=document.createElement('small');date.textContent=new Date(project.updated_at).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
      const open=document.createElement('button');open.type='button';open.className='secondary';open.textContent='Open project';
      open.setAttribute('aria-label',`Open project ${project.name}`);open.addEventListener('click',()=>openProject(project.id));
      footer.append(date,open);row.append(title,address,model,stats,footer);$('project-list').append(row);
    }
    controls();
  }
  async function loadProjects(){
    try{
      const data=await request('/api/projects');connected=data.configured;
      listProjects(data.projects);
      status(connected ? (data.projects.length ? `${data.projects.length} saved project${data.projects.length===1?'':'s'} · MongoDB Atlas` : 'Atlas connected. Save your first project after a search.') : data.message);
    }catch(e){status(e.message);}
    controls();
  }
  async function saveProject(){
    if(requestBusy || appBusy || !currentJob || !connected)return;
    if(zonesDirty()){status('Apply your no-panel zone changes before saving.');return;}
    const name=$('project-name').value.trim();
    if(!name){$('project-name').focus();return;}
    requestBusy=true;controls();status('Saving project to Atlas…');
    try{
      const data=await request('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:currentJob,name})});
      await loadProjects();status(`Saved “${data.project.name}” to MongoDB Atlas.`);toast('Project saved');
    }catch(e){status(e.message);}
    finally{requestBusy=false;controls();}
  }
  async function openProject(id){
    if(requestBusy || appBusy)return;
    requestBusy=true;busy(true);controls();status('Opening saved project…');
    try{
      const data=await request(`/api/projects/${encodeURIComponent(id)}/open`,{method:'POST'});
      restoreSearchInputs(data.result);render(data.result,data.job_id);
      $('project-name').value=data.project.name;
      await loadHistory();status(`Opened “${data.project.name}”.`);toast('Saved project opened');
    }catch(e){status(e.message);}
    finally{requestBusy=false;busy(false);controls();}
  }
  window.photogridProjects={
    setBusy(value){appBusy=value;controls();},
    render(result,job){
      if(activeJob!==job){activeJob=job;$('project-name').value=(result.demo ? 'Sample solar plan' : result.request.address).slice(0,80);}
      controls();
    },
    refresh:loadProjects,
  };
  $('project-save-form').addEventListener('submit',event=>{event.preventDefault();saveProject();});
  $('projects-refresh').addEventListener('click',loadProjects);
  $('save-project-shortcut').addEventListener('click',()=>{
    setTab('search');$('search-controls').open=true;$('saved-projects').open=true;
    $('project-name').focus();$('saved-projects').scrollIntoView({behavior:'smooth',block:'center'});
  });
  loadProjects();
})();
