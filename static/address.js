/* Address picker: debounced server requests, keyboard navigation and saved-search fallback. */
(() => {
  const input=document.getElementById('address');
  const dropdown=document.getElementById('address-dropdown');
  const list=document.getElementById('address-options');
  const help=document.getElementById('address-help');
  const attribution=document.getElementById('address-attribution');
  let options=[],active=-1,timer,controller,version=0,recent=[];
  function close(){
    clearTimeout(timer);controller?.abort();version++;
    dropdown.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');active=-1;
  }
  function recentMatches(){
    const query=input.value.trim().toLowerCase();
    return recent.filter(address=>!query || address.toLowerCase().includes(query)).slice(0,5)
      .map(address=>({address,label:address,detail:'Previously analyzed',source:'recent'}));
  }
  function choose(item){input.value=item.address;close();help.textContent='Address selected. Set your radius and budget below.';document.getElementById('radius').focus();}
  function render(items){
    options=items;active=-1;list.replaceChildren();input.removeAttribute('aria-activedescendant');
    for(const [index,item] of items.entries()){
      const option=document.createElement('li');option.id=`address-option-${index}`;option.setAttribute('role','option');option.setAttribute('aria-selected','false');
      const label=document.createElement('strong');label.textContent=item.label || item.address;
      const detail=document.createElement('span');detail.textContent=item.detail || 'Select this address';
      option.append(label,detail);option.addEventListener('mousedown',event=>event.preventDefault());option.addEventListener('click',()=>choose(item));list.append(option);
    }
    attribution.hidden=!items.some(item=>item.source==='google');
    dropdown.hidden=items.length===0;input.setAttribute('aria-expanded',String(items.length>0));
  }
  function activate(index){
    active=(index+options.length)%options.length;
    [...list.children].forEach((item,i)=>item.setAttribute('aria-selected',String(i===active)));
    input.setAttribute('aria-activedescendant',list.children[active].id);list.children[active].scrollIntoView({block:'nearest'});
  }
  function schedule(){
    close();const query=input.value.trim();render(recentMatches());
    if(query.length<3){help.textContent='Type at least 3 characters for live suggestions, or choose a recent address.';return;}
    const requestVersion=version;
    timer=setTimeout(async()=>{
      controller=new AbortController();help.textContent='Finding address suggestions…';
      try{
        const response=await fetch('/api/address-suggestions',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({query,api_key:document.getElementById('api-key').value}),signal:controller.signal});
        const data=await response.json();if(!response.ok)throw new Error(data.error || 'Suggestions are unavailable.');
        if(requestVersion!==version || document.activeElement!==input)return;
        render(data.suggestions || []);help.textContent=data.notice || 'Use ↑ / ↓ and Enter to choose an address, or click a suggestion.';
      }catch(error){
        if(error.name==='AbortError' || requestVersion!==version || document.activeElement!==input)return;
        render(recentMatches());help.textContent='Suggestions are unavailable. You can still type the full street, city, state and ZIP.';
      }
    },400);
  }
  input.addEventListener('input',schedule);
  input.addEventListener('focus',()=>{render(recentMatches());if(input.value.trim().length>=3)schedule();});
  input.addEventListener('blur',close);
  input.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();close();return;}
    if(dropdown.hidden || !options.length)return;
    if(event.key==='ArrowDown'){event.preventDefault();activate(active+1);}
    else if(event.key==='ArrowUp'){event.preventDefault();activate(active<0 ? options.length-1 : active-1);}
    else if(event.key==='Enter' && active>=0){event.preventDefault();choose(options[active]);}
    else if(event.key==='Tab')close();
  });
  document.getElementById('api-key').addEventListener('change',()=>{if(document.activeElement===input)schedule();});
  window.photogridAddress={close,setRecent(addresses){recent=[...new Set(addresses)];}};
})();
