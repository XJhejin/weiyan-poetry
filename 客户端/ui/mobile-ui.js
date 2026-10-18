'use strict';
// Phone layout also works in desktop previews. Native Android handles its Back key here.
window.weiyanBack=()=>{
  if($('#modal').open){closeModal();return true;}
  if(!$('#emoji-picker').hidden){$('#emoji-picker').hidden=true;return true;}
  if($('#app').classList.contains('mobile-chat')&&innerWidth<=620){saveDraft();$('#app').classList.remove('mobile-chat');return true;}
  return false;
};
if(/WeiyanAndroid/.test(navigator.userAgent)){
  document.body.classList.add('android-app');
  $('#composer-hint').textContent='消息在本机加密后发送';
  // On phones Enter inserts a line break; the existing send button sends the message.
  $('#message-input').addEventListener('keydown',e=>{if(e.key==='Enter')e.stopImmediatePropagation();},true);
}
if(window.visualViewport){
  const fit=()=>document.documentElement.style.setProperty('--app-height',Math.round(visualViewport.height)+'px');
  visualViewport.addEventListener('resize',fit);fit();
}
