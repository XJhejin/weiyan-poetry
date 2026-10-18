'use strict';
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
      settings.innerHTML='<div>后台运行设置<p>建议在应用的电池设置中允许后台运行，并在手机管家中允许自启动。强行停止后需重新打开客户端。</p></div><button id="open-background-settings">前往设置</button>';
      toggle.parentElement.after(row,settings);
      const alerts=document.createElement('div');alerts.className='settings-row';alerts.innerHTML='<div>消息弹窗<p>后台新消息使用系统横幅，前台其他会话显示加密消息提示；免打扰会话保持静默。</p></div><button id="message-popup-settings">通知设置</button>';settings.after(alerts);
      $('#message-popup-settings').onclick=()=>post('/desktop/background',{action:'notification-settings'}).catch(e=>toast(e.message));
      const render=data=>{const control=$('#background-toggle');if(!control)return;control.classList.toggle('on',data.enabled);control.setAttribute('aria-checked',String(data.enabled));$('#background-status').textContent=!data.enabled?'已停止后台接收，重新开启后补收消息。':(data.running?'后台加密接收服务正在运行。':'服务正在启动。')+(data.battery_unrestricted?' 已允许不受电池优化限制。':' 请在系统中允许后台运行。');};
      const refresh=()=>api('/desktop/background').then(render).catch(e=>toast(e.message));refresh();
      $('#background-toggle').onclick=async()=>{const control=$('#background-toggle');control.disabled=true;try{render(await post('/desktop/background',{enabled:control.getAttribute('aria-checked')!=='true'}));}catch(e){toast(e.message);}finally{control.disabled=false;}};
      $('#open-background-settings').onclick=()=>post('/desktop/background',{action:'settings'}).catch(e=>toast(e.message));
      const onVisible=()=>{if(!$('#background-settings'))document.removeEventListener('visibilitychange',onVisible);else if(!document.hidden)refresh();};
      document.addEventListener('visibilitychange',onVisible);
    }
  }).catch(()=>{});
};
$('#settings-button').onclick=()=>openSettings();
