'use strict';
const networkStatus=document.createElement('button');networkStatus.className='client-network-status';networkStatus.id='client-network-status';networkStatus.textContent='服务器延迟 — · 探测丢失率 —';networkStatus.title='最近 30 次 HTTPS/HTTP 健康探测的往返耗时和失败比例，复用聊天连接；首次连接或断线重连包含握手时间。每 15 秒探测一次，并非 ICMP 丢包率。点击立即刷新。';
$('#connection-text').parentElement.append(networkStatus);
let networkChecking=false;
async function refreshNetworkStatus(){
  if(networkChecking||document.hidden)return;networkChecking=true;
  try{const n=await api('/desktop/network');networkStatus.textContent=`延迟 ${n.latency_ms===null?'—':Math.round(n.latency_ms)+' ms'} · 探测丢失 ${n.loss_rate===null?'—':n.loss_rate+'%'}`;networkStatus.setAttribute('aria-label',`服务器连接质量，${networkStatus.textContent}，已探测 ${n.samples} 次`);networkStatus.dataset.samples=String(n.samples);}
  catch{networkStatus.textContent='服务器连接质量暂不可用';}
  finally{networkChecking=false;}
}
networkStatus.onclick=refreshNetworkStatus;setInterval(refreshNetworkStatus,15000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshNetworkStatus();});refreshNetworkStatus();
