/* Solar advisor chat. The server adds the site data and runs what-if scenarios; this file only keeps the conversation. */
(() => {
  const MAX_HISTORY=12, MAX_ASSISTANT_CHARS=3000, MAX_REQUEST_BYTES=14000, TIMEOUT_MS=120000;
  const STARTERS=['Is this a good investment?','Why this panel over the others?','How can I meet my energy target?','What if my budget were $10,000 higher?','What should I ask installers?'];
  const PRIORITY={balanced:'balanced priority',lowest_cost:'lowest-cost priority',space_efficiency:'least-space priority',longer_warranty:'longest-warranty priority'};
  const root=$('advisor'), panel=$('advisor-panel'), log=$('advisor-log'), input=$('advisor-input');
  const configured=root.dataset.configured==='true';
  let history=[], job=null, planKey='', waiting=false, appBusy=false;

  const scrollDown=()=>{log.scrollTop=log.scrollHeight;};
  function controls(){
    $('advisor-send').disabled=waiting || appBusy || !job || !input.value.trim();
    $('advisor-starters').querySelectorAll('button').forEach(button=>button.disabled=waiting || appBusy || !job);
  }
  function resize(){input.style.height='auto';input.style.height=`${Math.min(input.scrollHeight,120)}px`;}

  // Model text becomes DOM nodes (never innerHTML): paragraphs, lists, **bold**, `code` and https links.
  function inline(text){
    const fragment=document.createDocumentFragment(); let last=0;
    for(const match of text.matchAll(/\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https:\/\/[^\s)]+)\)|https:\/\/[^\s)<>]+/g)){
      fragment.append(text.slice(last,match.index)); last=match.index+match[0].length;
      if(match[1] || match[2]){const node=document.createElement(match[1] ? 'strong' : 'code');node.textContent=match[1] || match[2];fragment.append(node);continue;}
      const url=match[4] || match[0].replace(/[.,;:!?]+$/,''), link=document.createElement('a');
      link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=match[3] || url;fragment.append(link);
      if(!match[4])fragment.append(match[0].slice(url.length));
    }
    fragment.append(text.slice(last));
    return fragment;
  }
  function formatReply(text){
    const fragment=document.createDocumentFragment(); let list=null, lines=[];
    const flush=()=>{
      if(!lines.length)return;
      const paragraph=document.createElement('p');
      lines.forEach((line,index)=>{if(index)paragraph.append(document.createElement('br'));paragraph.append(inline(line));});
      fragment.append(paragraph);lines=[];
    };
    for(const raw of text.split('\n')){
      const line=raw.trim(), bullet=line.match(/^[-*•]\s+(.*)$/), step=line.match(/^(\d+)[.)]\s+(.*)$/), heading=line.match(/^#{1,6}\s+(.*)$/);
      if(!line){flush();list=null;continue;}
      if(bullet || step){
        flush();const tag=bullet ? 'UL' : 'OL';
        if(list?.tagName!==tag){list=document.createElement(tag);if(step && step[1]!=='1')list.start=Number(step[1]);fragment.append(list);}
        const item=document.createElement('li');item.append(inline(bullet ? bullet[1] : step[2]));list.append(item);continue;
      }
      list=null;
      if(heading){flush();const paragraph=document.createElement('p'),strong=document.createElement('strong');strong.textContent=heading[1].replaceAll('**','');paragraph.append(strong);fragment.append(paragraph);continue;}
      lines.push(line);
    }
    flush();
    return fragment;
  }
  function scenarioText(scenario){
    const s=scenario.settings || {}, changed=new Set(scenario.changed || []);
    const parts=[];
    if(changed.has('budget_usd'))parts.push(`${dollars(s.budget_usd)} budget`);
    if(changed.has('energy_target_kwh_year'))parts.push(`${format(s.energy_target_kwh_year)} kWh/year target`);
    if(changed.has('priority'))parts.push(PRIORITY[s.priority] || s.priority);
    if(changed.has('space_limit_m2'))parts.push(s.space_limit_m2 ? `${format(s.space_limit_m2)} m² max panel area` : 'no panel-area limit');
    if(changed.has('system_derate'))parts.push(`${format(s.system_derate*100)}% system efficiency`);
    if(changed.has('budget_includes_installation'))parts.push(s.budget_includes_installation ? 'budget includes installation' : 'panels-only budget');
    return `Ran PhotoGrid's planner: ${parts.length ? parts.join(' · ') : 'current settings'}`;
  }
  function bubble(kind,text,scenarios=[]){
    const node=document.createElement('div');node.className=`advisor-message ${kind}`;
    if(kind==='assistant')node.append(formatReply(text));else node.textContent=text;
    for(const scenario of scenarios){const note=document.createElement('small');note.className='advisor-scenario';note.textContent=scenarioText(scenario);node.append(note);}
    log.append(node);scrollDown();
    return node;
  }
  function note(text){const node=document.createElement('p');node.className='advisor-note';node.textContent=text;log.append(node);scrollDown();}
  function typing(){
    const node=document.createElement('div');node.className='advisor-message assistant advisor-typing';
    const label=document.createElement('span');label.className='sr-only';label.textContent='Advisor is thinking';
    node.append(label);for(let i=0;i<3;i++)node.append(document.createElement('i'));
    log.append(node);scrollDown();
    return node;
  }
  function greeting(result){
    const plan=result.proposal || {}, model=plan.panel_model;
    const place=result.demo ? 'the sample neighborhood (synthetic data)' : streetCityState(result.request);
    const layout=model && plan.panel_count ? `the ${plan.panel_count}-panel ${panelName(model)} layout, ` : '';
    return `Hi! I can explain the plan for ${place}: ${layout}its cost, savings and payback, and how it compares with every other panel model. I can also test what-ifs with PhotoGrid's planner. Ask me anything, or try a suggestion below.`;
  }
  function reset(result){
    history=[];log.replaceChildren();
    bubble('assistant',configured ? greeting(result) : 'The solar advisor needs a DeepSeek API key. Add DEEPSEEK_API_KEY to the .env file and restart PhotoGrid.');
    $('advisor-starters').replaceChildren();
    $('advisor-starters').hidden=!configured;
    for(const question of STARTERS){
      const button=document.createElement('button');button.type='button';button.textContent=question;
      button.addEventListener('click',()=>send(question));$('advisor-starters').append(button);
    }
    input.disabled=!configured;controls();
  }
  // Newest messages first, within the server's message count and request-size limits.
  function trimmed(){
    let messages=history.slice(-MAX_HISTORY).map(m=>({role:m.role,content:m.role==='assistant' ? m.content.slice(0,MAX_ASSISTANT_CHARS) : m.content}));
    const bytes=list=>new TextEncoder().encode(JSON.stringify({messages:list})).length;
    while(messages.length>1 && bytes(messages)>MAX_REQUEST_BYTES)messages=messages.slice(1);
    return messages;
  }
  async function send(text){
    text=text.trim();
    if(!text || waiting || appBusy || !job || !configured)return;
    const askedJob=job;
    history.push({role:'user',content:text});
    const question=bubble('user',text);input.value='';resize();
    $('advisor-starters').hidden=true;
    waiting=true;controls();
    const indicator=typing(), controller=new AbortController(), timer=setTimeout(()=>controller.abort(),TIMEOUT_MS);
    try{
      const response=await fetch(`/api/advisor/${encodeURIComponent(askedJob)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:trimmed()}),signal:controller.signal});
      const data=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(data.error || 'The advisor could not answer. Try again.');
      if(askedJob!==job)return;
      history.push({role:'assistant',content:data.reply});
      bubble('assistant',data.reply,data.scenarios || []);
    }catch(e){
      if(askedJob!==job)return;
      // Unanswered questions leave the history, and the text goes back in the box to retry.
      history.pop();question.remove();
      bubble('error',e.name==='AbortError' ? 'The advisor took too long to answer. Try again.' : e.message);
      if(!input.value)input.value=text;resize();
    }finally{
      clearTimeout(timer);indicator.remove();waiting=false;controls();
    }
  }
  function open(value){
    panel.hidden=!value;$('advisor-toggle').hidden=value;
    $('advisor-toggle').setAttribute('aria-expanded',String(value));
    if(value){scrollDown();if(!input.disabled)input.focus();}else $('advisor-toggle').focus();
  }

  $('advisor-toggle').addEventListener('click',()=>open(true));
  $('advisor-close').addEventListener('click',()=>open(false));
  panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.stopPropagation();open(false);}});
  input.addEventListener('input',()=>{resize();controls();});
  input.addEventListener('keydown',event=>{
    if(event.key==='Enter' && !event.shiftKey && !event.isComposing){event.preventDefault();send(input.value);}
  });
  $('advisor-form').addEventListener('submit',event=>{event.preventDefault();send(input.value);});

  window.photogridAdvisor={
    setBusy(value){appBusy=value;controls();},
    render(result,newJob){
      root.hidden=false;
      const proposal=result.proposal || {}, settings=result.panel_recommendations || {};
      const key=[proposal.panel_model?.model_id,proposal.panel_count,settings.budget_usd,settings.energy_target_kwh_year,settings.priority,
        (result.blocked_zones || []).length,(result.roof_selection?.excluded_roof_ids || []).length,result.assumptions?.system_derate].join('|');
      if(newJob!==job){job=newJob;planKey=key;reset(result);}
      else if(key!==planKey){planKey=key;if(history.length)note('Plan updated. New answers use the updated layout.');}
    },
  };
})();
