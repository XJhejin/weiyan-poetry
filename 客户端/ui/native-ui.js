'use strict';
const originalOpenSettings=openSettings;
openSettings=function(){
  originalOpenSettings();
  api('/desktop/notifications').then(info=>{
    const toggle=$('#notification-toggle');
    if(!toggle||!info.native)return;
    toggle.classList.toggle('on',info.enabled);toggle.setAttribute('aria-pressed',String(info.enabled));
    const copy=toggle.previousElementSibling;
    if(copy)copy.innerHTML='系统消息通知<p>后台接收加密消息，通知中不显示聊天正文</p>';
    toggle.onclick=async()=>{try{const result=await post('/desktop/notifications',{enabled:!toggle.classList.contains('on')});toggle.classList.toggle('on',result.enabled);toggle.setAttribute('aria-pressed',String(result.enabled));}catch(e){toast(e.message);}};
  }).catch(()=>{});
};
$('#settings-button').onclick=()=>openSettings();
