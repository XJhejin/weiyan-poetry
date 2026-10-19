'use strict';
const connectButton=document.createElement('button');connectButton.id='connection-settings';connectButton.textContent='连接设置';connectButton.className='connection-settings';$('main header').append(connectButton);
const network=document.createElement('p');network.id='management-network';network.className='management-network';network.textContent='管理连接延迟 — · 探测丢失率 —';network.title='最近 30 次 SSH 管理状态请求的往返耗时与失败比例，包含 SSH 握手和服务处理时间；并非 ICMP 丢包率。';$('main header').after(network);
const connectionDialog=document.createElement('dialog');connectionDialog.id='connection-dialog';connectionDialog.className='connection-dialog';
connectionDialog.innerHTML='<div class="connection-heading"><h2>手动连接服务器</h2><button id="connection-close" aria-label="关闭连接设置">×</button></div><p>使用 SSH 连接已部署未言诗集管理组件的服务器。聊天端口 23074 与管理 SSH 端口不同。</p><form id="connection-form"><label>服务器地址<input id="ssh-host" required maxlength="253" placeholder="156.225.28.175" autocomplete="off"></label><div class="connection-columns"><label>SSH 端口<input id="ssh-port" type="number" min="1" max="65535" value="22" required></label><label>用户名<input id="ssh-user" required maxlength="64" value="root" autocomplete="off"></label></div><label>管理授权<select id="ssh-identity-mode"><option value="existing">使用此电脑已有授权</option><option value="import">导入已登记的管理密钥</option></select></label><div id="ssh-key-row" hidden><label>OpenSSH 管理私钥<input id="ssh-key-file" type="file"></label><p>密钥须已在服务器登记为受限管理授权。本机使用 Windows 账号加密保存，不上传 GitHub；不要选择聊天加密密钥。</p></div><p id="ssh-existing-status"></p><div class="connection-fingerprint"><button id="ssh-scan" type="button">读取主机指纹</button><code id="ssh-fingerprint">尚未读取</code><label class="checkbox"><input type="checkbox" id="ssh-trust">我已通过可信渠道核对主机指纹</label></div><p id="connection-error" class="error" role="status"></p><div class="actions"><button id="ssh-connect" class="primary" type="submit">保存并连接</button><button id="ssh-retry" type="button">重试当前连接</button></div><p>连接成功后才保存设置。公开安装包不包含服务器管理授权。</p></form>';
document.body.append(connectionDialog);
let connectionKey='',connectionIdentity='',connectionBusy=false;
const fields=()=>({host:$('#ssh-host').value.trim(),port:Number($('#ssh-port').value),user:$('#ssh-user').value.trim()});
function invalidateFingerprint(){connectionKey='';$('#ssh-trust').checked=false;$('#ssh-fingerprint').textContent='地址已更改，请重新读取并核对指纹。';}
$('#ssh-host').oninput=$('#ssh-port').oninput=invalidateFingerprint;
$('#ssh-identity-mode').onchange=()=>{$('#ssh-key-row').hidden=$('#ssh-identity-mode').value!=='import';};
async function openConnection(){
  $('#connection-error').textContent='';connectionIdentity='';$('#ssh-key-file').value='';$('#ssh-trust').checked=false;
  WeiyanMotion.openDialog(connectionDialog);
  try{
    const info=await api('/manage/connection');$('#ssh-host').value=info.host;$('#ssh-port').value=info.port;$('#ssh-user').value=info.user;
    connectionKey=info.host_key||'';$('#ssh-fingerprint').textContent=info.fingerprint||'尚未读取';
    $('#ssh-existing-status').textContent=info.has_identity?'此 Windows 账号已保存管理授权。':'此 Windows 账号尚未配置管理授权，请导入已在服务器登记的密钥。';
    $('#ssh-identity-mode').value=info.has_identity?'existing':'import';$('#ssh-identity-mode').onchange();
  }catch(error){$('#connection-error').textContent=error.message;}
}
connectButton.onclick=openConnection;
$('.header-state').setAttribute('role','button');$('.header-state').tabIndex=0;$('.header-state').title='打开连接设置';$('.header-state').onclick=openConnection;$('.header-state').onkeydown=e=>{if(e.key==='Enter'||e.key===' ')openConnection();};
$('#connection-close').onclick=()=>{if(!connectionBusy){connectionIdentity='';$('#ssh-key-file').value='';WeiyanMotion.closeDialog(connectionDialog);}};
connectionDialog.addEventListener('cancel',event=>{if(connectionBusy)event.preventDefault();else{connectionIdentity='';$('#ssh-key-file').value='';}});
$('#ssh-key-file').onchange=async()=>{
  connectionIdentity='';const file=$('#ssh-key-file').files[0];if(!file)return;
  if(file.size>32768){$('#connection-error').textContent='请选择有效的 OpenSSH 管理私钥。';$('#ssh-key-file').value='';return;}
  connectionIdentity=await file.text();
};
function disableConnection(disabled){connectionBusy=disabled;$$('#connection-form button,#connection-form input,#connection-form select').forEach(e=>e.disabled=disabled);}
$('#ssh-scan').onclick=async()=>{
  disableConnection(true);$('#connection-error').textContent='正在读取服务器主机指纹…';
  try{const result=await api('/manage/connection/scan',fields());connectionKey=result.host_key;$('#ssh-fingerprint').textContent=result.fingerprint;$('#ssh-trust').checked=false;$('#connection-error').textContent='请与服务器管理员提供的主机指纹核对。';}
  catch(error){$('#connection-error').textContent=error.message;}
  finally{disableConnection(false);}
};
$('#connection-form').onsubmit=async event=>{
  event.preventDefault();if(connectionBusy)return;
  const payload={...fields(),host_key:connectionKey,trust_host:$('#ssh-trust').checked,private_key:$('#ssh-identity-mode').value==='import'?connectionIdentity:''};
  if($('#ssh-identity-mode').value==='import'&&!connectionIdentity){$('#connection-error').textContent='请先选择已登记的管理私钥。';return;}
  disableConnection(true);$('#connection-error').textContent='正在验证管理授权，成功后保存连接…';
  try{await api('/manage/connection/connect',payload);connectionIdentity='';$('#ssh-key-file').value='';WeiyanMotion.closeDialog(connectionDialog);toast('服务器已连接');await refresh();await refreshHost();}
  catch(error){$('#connection-error').textContent=error.message;}
  finally{payload.private_key='';disableConnection(false);}
};
$('#ssh-retry').onclick=async()=>{if(connectionBusy)return;disableConnection(true);$('#connection-error').textContent='正在重新连接…';try{await api('/manage/state');$('#connection-error').textContent='管理连接正常。';await refresh();await refreshHost();}catch(error){$('#connection-error').textContent=error.message;}finally{disableConnection(false);}};
async function refreshNetwork(){try{const n=await api('/manage/network');network.textContent=`管理连接延迟 ${n.latency_ms===null?'—':Math.round(n.latency_ms)+' ms'} · 探测丢失率 ${n.loss_rate===null?'—':n.loss_rate+'%'} · ${n.samples}/${n.window} 次`;}catch{network.textContent='管理连接数据暂不可用';}}
refreshNetwork();setInterval(refreshNetwork,5000);
