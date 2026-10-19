'use strict';
if(!window.weiyanOpenNotification){
  let pendingConversation='';
  window.weiyanOpenNotification=async cid=>{
    if(typeof cid!=='string'||!cid||cid.length>128)return;
    pendingConversation=cid;
    if(!state.me)return;
    pendingConversation='';state.view='messages';
    if($('#modal').open)await closeModal();
    try{await selectConversation(cid);}catch(error){toast(error.message);}
  };
  const beforeNotificationBootstrap=bootstrap;
  bootstrap=async function(options){await beforeNotificationBootstrap(options);if(pendingConversation&&state.me)await window.weiyanOpenNotification(pendingConversation);};
}
const originalOpenSettings=openSettings;
openSettings=function(){
  originalOpenSettings();
  api('/desktop/notifications').then(info=>{
    const toggle=$('#notification-toggle');
    if(!toggle||!info.native)return;
    if(window.weiyanSetMessageNotices)window.weiyanSetMessageNotices(info.enabled);
    toggle.classList.toggle('on',info.enabled);toggle.setAttribute('aria-pressed',String(info.enabled));
    const copy=toggle.previousElementSibling;
    if(copy)copy.innerHTML='系统消息通知<p>后台接收加密消息，通知中不显示聊天正文</p>';
    toggle.onclick=async()=>{try{const result=await post('/desktop/notifications',{enabled:!toggle.classList.contains('on')});toggle.classList.toggle('on',result.enabled);toggle.setAttribute('aria-pressed',String(result.enabled));if(window.weiyanSetMessageNotices)window.weiyanSetMessageNotices(result.enabled);}catch(e){toast(e.message);}};
    if(info.platform==='windows'&&!$('#tray-settings')){
      const row=document.createElement('div');row.className='settings-row';
      row.innerHTML='<div>托盘图标常驻<p>在 Windows 任务栏设置中开启“未言诗集”，让新消息闪烁始终可见；也可将图标从折叠菜单拖到任务栏。</p></div><button id="tray-settings">前往设置</button>';
      toggle.parentElement.after(row);
      $('#tray-settings').onclick=async()=>{try{await post('/desktop/tray-settings');}catch(e){toast(e.message);}};
    }
    if(info.platform==='android'&&!$('#background-settings')){
      const row=document.createElement('div');row.className='settings-row';
      row.innerHTML='<div>后台接收消息<p id="background-status">正在检查后台状态…</p></div><button class="notification-toggle" id="background-toggle" role="switch" aria-label="后台接收消息" aria-checked="false"></button>';
      const settings=document.createElement('div');settings.className='settings-row';settings.id='background-settings';
      settings.innerHTML='<div>后台运行设置<p>已移除常驻服务通知。厂商系统推送尚未开通，进程被系统结束后需重新打开应用补收消息。</p></div><button id="open-background-settings">前往设置</button>';
      toggle.parentElement.after(row,settings);
      const alerts=document.createElement('div');alerts.className='settings-row';alerts.innerHTML='<div>消息弹窗<p>后台新消息使用系统横幅，前台其他会话显示加密消息提示；免打扰会话仅在被明确 @ 时提醒。连续来信合并提示，通知中不显示正文。</p></div><button id="message-popup-settings">通知设置</button>';settings.after(alerts);
      $('#message-popup-settings').onclick=()=>post('/desktop/background',{action:'notification-settings'}).catch(e=>toast(e.message));
      const startup=document.createElement('div');startup.className='settings-row';startup.innerHTML='<div>自启动与后台运行<p>允许系统消息通知。后台连接仅在应用进程仍运行时有效，不等同于厂商离线推送。</p></div><button id="open-application-settings">应用设置</button>';settings.after(startup);
      $('#open-application-settings').onclick=()=>post('/desktop/background',{action:'application-settings'}).catch(e=>toast(e.message));
      const render=data=>{const control=$('#background-toggle');if(!control)return;control.classList.toggle('on',data.enabled);control.setAttribute('aria-checked',String(data.enabled));$('#background-status').textContent=!data.enabled?'已停止后台接收，重新开启后补收消息。':'常驻通知已移除 · 系统推送待开通；进程被结束后无法保证及时收信。';};
      const refresh=()=>api('/desktop/background').then(render).catch(e=>toast(e.message));refresh();
      $('#background-toggle').onclick=async()=>{const control=$('#background-toggle');control.disabled=true;try{render(await post('/desktop/background',{enabled:control.getAttribute('aria-checked')!=='true'}));}catch(e){toast(e.message);}finally{control.disabled=false;}};
      $('#open-background-settings').onclick=()=>post('/desktop/background',{action:'settings'}).catch(e=>toast(e.message));
      const onVisible=()=>{if(!$('#background-settings'))document.removeEventListener('visibilitychange',onVisible);else if(!document.hidden)refresh();};
      document.addEventListener('visibilitychange',onVisible);
      const onResume=()=>{if(!$('#background-settings'))window.removeEventListener('weiyanAndroidResume',onResume);else refresh();};
      window.addEventListener('weiyanAndroidResume',onResume);
    }
  }).catch(()=>{});
};
$('#settings-button').onclick=()=>openSettings();
