/* Progress follows server stages; elapsed time is real, with no invented percentage. */
(() => {
  const screen=document.getElementById('loading-screen');
  const dismiss=document.getElementById('loading-background');
  const message=document.getElementById('loading-message');
  const stages=[...document.querySelectorAll('#loading-steps li')];
  const page=[document.querySelector('header'),document.querySelector('main'),document.querySelector('footer')];
  let timer,started,returnFocus,stage=0;
  function hide(){
    const restoreFocus=!screen.hidden;screen.hidden=true;page.forEach(element=>element.inert=false);
    if(restoreFocus){
      if(returnFocus && !returnFocus.disabled)returnFocus.focus();
      else{const status=document.getElementById('status');status.tabIndex=-1;status.focus();}
    }
  }
  function showStage(index){
    stage=Math.max(stage,index);
    stages.forEach((element,i)=>{
      element.classList.toggle('done',i<stage);element.classList.toggle('active',i===stage);
      if(i===stage)element.setAttribute('aria-current','step');else element.removeAttribute('aria-current');
    });
  }
  window.photogridLoading={
    start(area,demo=false){
      clearInterval(timer);started=Date.now();stage=0;returnFocus=document.activeElement;
      document.getElementById('loading-title').textContent=demo ? 'Building the sample area' : 'Checking your search area';
      document.getElementById('loading-area').textContent=area;
      stages[0].textContent=demo ? 'Prepare the sample area' : 'Locate the address';
      stages[1].textContent=demo ? 'Load sample imagery and sunlight' : 'Load aerial and sunlight data';
      message.textContent='Preparing your analysis…';showStage(0);
      document.getElementById('loading-elapsed').textContent='Elapsed: 0:00';
      screen.hidden=false;page.forEach(element=>element.inert=true);screen.focus();
      timer=setInterval(()=>{const seconds=Math.floor((Date.now()-started)/1000);document.getElementById('loading-elapsed').textContent=`Elapsed: ${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;},1000);
    },
    update(text){
      message.textContent=text;
      if(/comparing|layout|complete/i.test(text))showStage(3);
      else if(/detecting|calculating|looking up roof/i.test(text))showStage(2);
      else if(/requesting|download|aligning|imagery/i.test(text))showStage(1);
    },
    finish(){clearInterval(timer);hide();}
  };
  dismiss.addEventListener('click',()=>{hide();document.getElementById('status').textContent='Analysis continues in the background.';});
  screen.addEventListener('keydown',event=>{
    if(event.key==='Tab'){event.preventDefault();dismiss.focus();}
    if(event.key==='Escape'){event.preventDefault();dismiss.click();}
  });
})();
