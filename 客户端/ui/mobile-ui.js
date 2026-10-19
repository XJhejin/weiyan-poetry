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
  let allowNotices=false,popupTimer,pendingConversation='';
  const popup=document.createElement('aside');popup.id='message-popup';popup.className='message-popup';popup.hidden=true;popup.setAttribute('role','status');
  popup.innerHTML='<img src="/assets/logo.svg" alt=""><button class="message-popup-open"><b>未言诗集</b><span>收到新的加密消息，点击查看。</span></button><button class="message-popup-close" aria-label="关闭消息提示">×</button>';document.body.append(popup);
  const dismiss=()=>{popup.hidden=true;clearTimeout(popupTimer);};
  window.weiyanSetMessageNotices=enabled=>{allowNotices=Boolean(enabled);if(!allowNotices)dismiss();};
  api('/desktop/notifications').then(i=>window.weiyanSetMessageNotices(i.enabled)).catch(()=>{});
  window.weiyanOpenNotification=cid=>{if(typeof cid!=='string'||cid.length>128)return;pendingConversation=cid;dismiss();if(state.me){pendingConversation='';state.view='messages';if($('#modal').open)closeModal();selectConversation(cid).catch(e=>toast(e.message));}};
  const originalBootstrap=bootstrap;
  bootstrap=async function(options){await originalBootstrap(options);if(pendingConversation&&state.me)window.weiyanOpenNotification(pendingConversation);};
  popup.querySelector('.message-popup-close').onclick=dismiss;
  popup.querySelector('.message-popup-open').onclick=()=>window.weiyanOpenNotification(popup.dataset.conversation);
  const originalReceiveMessage=receiveMessage;
  receiveMessage=function(message){
    const duplicate=observedMessages.has(messageIdentity(message))||state.messages.has(message.id)||messageCache.get(message.conversation_id)?.messages.has(message.id)||state.conversations.some(c=>c.last_message?.id===message.id);
    const show=allowNotices&&!duplicate&&!document.hidden&&state.me&&message.sender.id!==state.me.id&&(message.conversation_id!==state.active||!$('#app').classList.contains('mobile-chat'))&&!message.recalled&&!message.history_sync&&(!stored('muted',[]).includes(message.conversation_id)||mentionsMe(message));
    originalReceiveMessage(message);
    if(show){popup.dataset.conversation=message.conversation_id;popup.hidden=false;clearTimeout(popupTimer);popupTimer=setTimeout(dismiss,6000);}
  };
}
if(window.visualViewport){
  const fit=()=>document.documentElement.style.setProperty('--app-height',Math.round(visualViewport.height)+'px');
  visualViewport.addEventListener('resize',fit);fit();
}
