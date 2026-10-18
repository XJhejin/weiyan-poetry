'use strict';
// Native dialog semantics stay intact; only the visual closing is deferred.
window.WeiyanMotion=(()=>{
  const pending=new WeakMap();
  const reduced=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
  function openDialog(dialog){
    const previous=pending.get(dialog);
    if(previous){previous.cancel();pending.delete(dialog);}
    dialog.classList.remove('motion-closing');
    if(!dialog.open)dialog.showModal();
  }
  async function closeDialog(dialog){
    if(!dialog.open)return;
    const previous=pending.get(dialog);
    if(previous)return previous.finished.catch(()=>{});
    if(reduced()||!dialog.animate){dialog.close();return;}
    dialog.classList.add('motion-closing');
    const animation=dialog.animate([{opacity:1,transform:'translateY(0) scale(1)'},{opacity:0,transform:'translateY(7px) scale(.975)'}],{duration:150,easing:'cubic-bezier(.4,0,1,1)',fill:'forwards'});
    pending.set(dialog,animation);
    try{await animation.finished;}catch{return;}
    if(pending.get(dialog)!==animation)return;
    dialog.close();animation.cancel();pending.delete(dialog);dialog.classList.remove('motion-closing');
  }
  document.addEventListener('cancel',event=>{if(event.target instanceof HTMLDialogElement){event.preventDefault();closeDialog(event.target);}},true);
  return {openDialog,closeDialog};
})();
