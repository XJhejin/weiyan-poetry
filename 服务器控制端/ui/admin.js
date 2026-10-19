'use strict';
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state=null,timer,busy=false,update=null,listenDirty=false;
function toast(s){$('#toast').textContent=s;$('#toast').hidden=false;clearTimeout(timer);timer=setTimeout(()=>$('#toast').hidden=true,3500);}
async function api(path,data){const r=await fetch(path,{method:data?'POST':'GET',cache:'no-store',headers:{'Content-Type':'application/json','X-Weiyan-Admin':'desktop'},body:data?JSON.stringify(data):undefined});const v=await r.json();if(!r.ok)throw new Error(v.error||'操作失败');return v;}
const titles={logs:['运行日志','检查远程聊天服务的运行情况。'],overview:['运行概览','掌握服务状态，让每一次交流顺畅抵达。'],accounts:['账号与设备','管理成员身份与设备，让连接井然有序。'],privacy:['隐私与存储','明确保存规则，让数据各有所归。'],updates:['软件更新','查看正式版本，获取新的功能与修复。']};
function tab(id){$$('.page').forEach(p=>p.hidden=p.id!==id);$$('[data-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.tab===id));$('#page-title').textContent=titles[id][0];$('#page-description').textContent=titles[id][1];}
$$('[data-tab]').forEach(b=>b.onclick=()=>tab(b.dataset.tab));$$('[data-go]').forEach(b=>b.onclick=()=>tab(b.dataset.go));
function queueSize(bytes){return bytes>=1073741824?(bytes/1073741824).toFixed(2)+' GB':bytes>=1048576?(bytes/1048576).toFixed(1)+' MB':(bytes/1024).toFixed(1)+' KB';}
function date(v){return new Date(typeof v==='number'?v*1000:v).toLocaleString('zh-CN',{hour12:false});}
function accountAvatar(user){const icons={bunny:'🐰',cat:'🐱',bear:'🐻',flower:'🌼',moon:'🌙',sparkles:'✨'};return `<span class="account-avatar">${/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(user.avatar||'')?`<img src="${esc(user.avatar)}" alt="${esc(user.nickname)}的头像">`:icons[user.avatar]||'🌼'}</span>`;}
function confirmAction(title,text){$('#confirm-title').textContent=title;$('#confirm-text').textContent=text;WeiyanMotion.openDialog($('#confirm'));return new Promise(resolve=>{$('#confirm-cancel').onclick=()=>{WeiyanMotion.closeDialog($('#confirm'));resolve(false);};$('#confirm-ok').onclick=()=>{WeiyanMotion.closeDialog($('#confirm'));resolve(true);};$('#confirm').oncancel=()=>resolve(false);});}
async function refresh(){if(busy)return;try{state=await api('/manage/state');render();}catch(e){$('#service-state').textContent='管理连接暂时中断';$('#status-dot').classList.add('off');$('#service-error').textContent=e.message;}}
function updateListenMode(){const custom=$('#listen-host').value==='custom';$('#custom-listen').hidden=!custom;$('#listen-address').required=custom;}
function renderListenConfig(){
  if(!listenDirty||state.running){
    const custom=!['0.0.0.0','127.0.0.1'].includes(state.host);
    $('#listen-host').value=custom?'custom':state.host;
    $('#listen-address').value=custom?state.host:'';
    $('#listen-port').value=state.port;
    listenDirty=false;
  }
  for(const id of ['listen-host','listen-address','listen-port'])$('#'+id).disabled=true;
  updateListenMode();
}
function addressLabel(value){return '公网 HTTPS';} function unusedAddressLabel(value){const host=new URL(value).hostname.replace(/^\[|\]$/g,'');return host==='::1'||host.startsWith('127.')?'本机':['0.0.0.0','::'].includes(state.host)?'局域网':'指定地址';}
function render(){
  $('#service-state').textContent=state.running?'服务正在运行':'服务已停止';$('#status-dot').classList.toggle('off',!state.running);
  $('#stat-online').textContent=state.online;$('#stat-connections').textContent=state.sessions.length+' 个连接';$('#stat-accounts').textContent=state.accounts.length;$('#stat-queue').textContent=state.queued;$('#stat-size').textContent=queueSize(state.queue_bytes)+' 暂存空间';
  const t=state.uptime;$('#stat-uptime').textContent=[Math.floor(t/3600),Math.floor(t/60)%60,t%60].map(v=>String(v).padStart(2,'0')).join(':');
  renderListenConfig();
  $('#service-control').textContent=state.running?'停止服务':'启动服务';$('#control-note').textContent='后端通过本机回环地址连接 HTTPS 代理。';$('#service-error').textContent=state.error;
  $('#address-list').innerHTML=['https://156.225.28.175:23074'].map(a=>`<div class="address"><div><small>${addressLabel(a)}</small><br><code>${esc(a)}</code></div><button data-copy="${esc(a)}">复制</button></div>`).join('');
  $('#live-devices').innerHTML=state.devices.filter(d=>d.online&&!d.revoked).map(d=>`<div class="live-device"><span>♧</span><div><b>${esc(d.nickname)}</b><small>${esc(d.name)} · ${esc(d.id.slice(0,8))}</small></div></div>`).join('')||'<p>暂无在线设备，等待成员连接。</p>';
  renderAccounts();
  if(!$('#policy-form').contains(document.activeElement)){$('#allow-registration').checked=state.settings.registration==='1';}
  $('#data-path').textContent=state.data_dir;
}
function renderAccounts(){if(!state)return;const q=$('#account-search').value.toLowerCase();const accounts=state.accounts.filter(a=>(a.username+' '+a.nickname).toLowerCase().includes(q));$('#accounts-empty').hidden=accounts.length>0;
  $('#account-rows').innerHTML=accounts.map(a=>`<tr><td><div class="account-identity">${accountAvatar(a)}<div><b>${esc(a.nickname)}</b><small>@${esc(a.username)}</small></div></div></td><td><span class="pill${a.disabled?' rose':''}">${a.disabled?'已停用':state.sessions.some(s=>s.user_id===a.id)?'在线':'正常'}</span>${a.is_guest?' <span class="pill muted">访客</span>':''}</td><td>${state.devices.filter(d=>d.user_id===a.id&&!d.revoked).length} 台</td><td>${esc(date(a.created_at))}</td><td><button data-account="${a.id}" data-op="${a.disabled?'enable':'disable'}">${a.disabled?'启用':'停用'}</button><button data-account="${a.id}" data-op="disconnect">退出登录</button><button data-account="${a.id}" data-op="reset_avatar">重置头像</button></td></tr>`).join('');
  $('#device-rows').innerHTML=state.devices.filter(d=>!d.revoked&&(!q||(d.username+' '+d.nickname).toLowerCase().includes(q))).map(d=>`<tr><td>${esc(d.nickname)}<small>@${esc(d.username)}</small></td><td class="mono" title="${esc(d.id)}">${esc(d.id.slice(0,16))}…</td><td><span class="pill${d.online?'':' muted'}">${d.online?'在线':'离线'}</span></td><td>${esc(date(d.last_seen))}</td><td><button data-account="${d.user_id}" data-device="${d.id}" data-op="revoke">撤销设备</button></td></tr>`).join('');
}
$('#account-search').oninput=renderAccounts;
document.addEventListener('click',async e=>{const c=e.target.closest('[data-copy]');if(c){try{await navigator.clipboard.writeText(c.dataset.copy);toast('地址已复制');}catch{toast('请选中地址后按 Ctrl+C 复制');}return;}const b=e.target.closest('[data-op]');if(!b)return;
 const labels={reset_avatar:['重置成员头像','将该成员头像恢复为默认花朵，并同步到客户端。成员可以重新选择自己的头像。'],disable:['停用账号','停用后将退出该账号的所有连接，并暂停接收新消息。'],enable:['启用账号','启用后，该账号可重新登录。'],disconnect:['退出账号登录','将使该账号的所有设备退出登录，成员需要重新输入密码。访客账号请先转为正式账号。'],revoke:['撤销加密设备','该设备将无法继续接收消息，分配给它的离线密文将被移除。此操作不能恢复原设备密钥的权限。']};
 if(!await confirmAction(...labels[b.dataset.op]))return;try{await api('/manage/action',{action:b.dataset.op,user_id:b.dataset.account,device_id:b.dataset.device});toast('操作已完成');await refresh();}catch(err){toast(err.message);}});
$('#listen-host').onchange=()=>{listenDirty=true;updateListenMode();};
$('#listen-address').oninput=$('#listen-port').oninput=()=>{listenDirty=true;};
$('#service-control').onclick=async()=>{if(!state)return;const stop=state.running;if(stop&&!await confirmAction('停止聊天服务','当前连接将会断开；账号和未投递密文会保留，重新启动后可继续投递。'))return;if(!stop&&(!$('#listen-port').reportValidity()||($('#listen-host').value==='custom'&&!$('#listen-address').reportValidity())))return;busy=true;$('#service-control').disabled=true;try{await api('/manage/action',stop?{action:'stop'}:{action:'start',host:$('#listen-host').value==='custom'?$('#listen-address').value.trim():$('#listen-host').value,port:Number($('#listen-port').value)});listenDirty=false;toast(stop?'服务已停止':'服务已启动');}catch(e){toast(e.message);}finally{busy=false;$('#service-control').disabled=false;await refresh();}};
$('#policy-form').onsubmit=async e=>{e.preventDefault();try{await api('/manage/action',{action:'settings',registration:$('#allow-registration').checked});toast('设置已保存');await refresh();}catch(err){toast(err.message);}};
$('#check-update').onclick=async()=>{const b=$('#check-update');b.disabled=true;$('#update-state').textContent='正在连接 GitHub 检查更新…';try{update=await api('/manage/update/check',{});$('#current-version').textContent='当前 '+update.current;$('#update-state').textContent=update.message;$('#update-repo').textContent=update.repository;$('#update-details').hidden=!update.latest;$('#latest-version').textContent=update.latest?'版本 '+update.latest:'';$('#release-notes').textContent=update.notes||'';$('#install-update').hidden=!update.available;}catch(e){$('#update-state').textContent=e.message;}finally{b.disabled=false;}};
$('#install-update').onclick=async()=>{if(!await confirmAction('安装服务端更新','将在远程服务器下载并验证签名，短暂重启聊天服务。账号与未投递密文会保留；启动失败自动恢复原版本。'))return;$('#install-update').disabled=true;$('#update-progress').hidden=false;$('#update-progress').textContent='正在启动远程更新任务，关闭控制端不会中断更新…';try{await api('/manage/update/install',{});const poll=setInterval(async()=>{try{const p=await api('/manage/update/status');$('#update-progress').textContent=p.message+(p.stage==='downloading'?' · '+p.percent+'%':'');if(['error','complete'].includes(p.stage)){clearInterval(poll);$('#install-update').disabled=false;await refresh();await refreshHost();if(p.stage==='complete')$('#install-update').hidden=true;}}catch{clearInterval(poll);}},2500);}catch(e){$('#update-progress').textContent=e.message;$('#install-update').disabled=false;}};
refresh();setInterval(refresh,8000);

api("/manage/update/info").then(i=>{$$(".version").forEach(e=>e.textContent=i.current);$("#current-version").textContent="当前 "+i.current;$("#update-repo").textContent=i.repository;}).catch(()=>{});

function bytes(v){return (v/1073741824).toFixed(2)+' GB';}
let hostBusy=false;
async function refreshHost(){if(hostBusy)return;hostBusy=true;try{const h=await api('/manage/host');$('#host-detail').textContent=h.hostname+' · '+h.system+' · '+h.address;$('#memory-usage').textContent=bytes(h.memory_total-h.memory_available)+' / '+bytes(h.memory_total);$('#memory-meter').value=100*(1-h.memory_available/h.memory_total);$('#disk-usage').textContent=bytes(h.disk_total-h.disk_free)+' / '+bytes(h.disk_total);$('#disk-meter').value=100*(1-h.disk_free/h.disk_total);$('#host-load').textContent=h.load.map(v=>v.toFixed(2)).join(' / ');$('#proxy-state').textContent=h.proxy.ActiveState==='active'?'HTTPS 代理正在运行':'HTTPS 代理已停止';$('#current-version').textContent='远程服务端 '+h.version;$$('.version').forEach(e=>e.textContent=h.version);if(h.update.stage!=='idle'){$('#update-progress').hidden=false;$('#update-progress').textContent=h.update.message+(h.update.stage==='downloading'?' · '+h.update.percent+'%':'');}}catch(e){$('#host-detail').textContent=e.message;}finally{hostBusy=false;}}
$('#restart-service').onclick=async()=>{if(!await confirmAction('重启远程聊天服务','客户端会短暂断开并自动重连，账号和待投递密文保留。'))return;const b=$('#restart-service');b.disabled=true;try{await api('/manage/restart',{});toast('远程服务已重新启动');await refresh();await refreshHost();}catch(e){toast(e.message);}finally{b.disabled=false;}};
$('#refresh-logs').onclick=async()=>{const b=$('#refresh-logs');b.disabled=true;try{$('#server-logs').textContent=(await api('/manage/logs')).text||'暂无运行日志。';}catch(e){$('#server-logs').textContent=e.message;}finally{b.disabled=false;}};
refreshHost();setInterval(refreshHost,12000);
