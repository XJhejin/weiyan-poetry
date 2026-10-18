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
    if(info.platform==='windows'&&!$('#tray-settings')){
      const row=document.createElement('div');row.className='settings-row';
      row.innerHTML='<div>托盘图标常驻<p>在 Windows 任务栏设置中开启“未言诗集”，让新消息闪烁始终可见；也可将图标从折叠菜单拖到任务栏。</p></div><button id="tray-settings">前往设置</button>';
      toggle.parentElement.after(row);
      $('#tray-settings').onclick=async()=>{try{await post('/desktop/tray-settings');}catch(e){toast(e.message);}};
    }
  }).catch(()=>{});
};
$('#settings-button').onclick=()=>openSettings();
