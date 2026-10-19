'use strict';
const MAX_ATTACHMENT_SIZE=10*1024**3, PREVIEW_IMAGE_SIZE=20*1024**2;
const attachmentStates=new Map();
let attachmentTask=null;
function fileSize(bytes){return bytes>=1073741824?(bytes/1073741824).toFixed(2)+' GB':bytes>=1048576?(bytes/1048576).toFixed(1)+' MB':bytes>=1024?(bytes/1024).toFixed(1)+' KB':bytes+' B';}
function attachmentContent(file,ready){
  const url=escape(file.url),name=escape(file.name);
  if(!ready)return `<span class="file-icon">${icons('copy')}</span><span><b>${name}</b><small data-transfer-progress>正在加密接收…</small></span>`;
  const caption=`<a class="file-caption" href="${url}?download=1" download="${name}">${name} · ${fileSize(file.size)} ↓</a>`;
  if(file.kind==='video')return `<video class="message-video" src="${url}" controls preload="metadata" playsinline aria-label="${name}"></video>${caption}`;
  if(file.kind==='image'&&file.size<=PREVIEW_IMAGE_SIZE)return `<img class="message-image" src="${url}" loading="lazy" alt="${name}">${caption}`;
  return `<a class="file-attachment" href="${url}?download=1" download="${name}"><span class="file-icon">${icons('copy')}</span><span><b>${name}</b><small>${fileSize(file.size)} · 保存文件</small></span><span>↓</span></a>`;
}
function attachmentHTML(file){
  if(!file)return '';
  const id=file.url?.split('/').pop();
  const ready=!file.streamed||file.ready||attachmentStates.get(id)?.ready;
  const encoded=escape(JSON.stringify(file));
  return `<div class="${file.kind==='video'?'video-attachment':'attachment-container'}" data-attachment="${encoded}" data-asset="${escape(id)}" data-ready="${!!ready}">${attachmentContent(file,ready)}</div>`;
}
function previewAttachment(result,video){
  state.attachment=null;state.fileAttachment=result;
  const picture=result.kind==='image'&&result.size<=PREVIEW_IMAGE_SIZE;
  $('#attachment-preview').innerHTML=`${video?`<video class="attachment-video-preview" src="${escape(result.url)}" controls preload="metadata" playsinline></video>`:picture?`<img src="${escape(result.url)}" alt="待发送的图片">`:`<span class="file-icon">${icons('copy')}</span>`}<span><b>${escape(result.name)}</b><small>${fileSize(result.size)} · ${video?'视频预览':'文件准备就绪'}</small></span><button id="cancel-file" aria-label="移除附件">×</button>`;
  $('#attachment-preview').hidden=false;
  $('#cancel-file').onclick=()=>{state.fileAttachment=null;$('#attachment-preview').hidden=true;$('#attachment-preview').innerHTML='';if(result.streamed)post('/desktop/attachments/cancel',{id:result.id}).catch(()=>{});};
}
async function cancelAttachmentTask(){
  const task=attachmentTask;if(!task)return;
  task.cancelled=true;task.controller?.abort();
  await task.done;
  if(task.asset)await post('/desktop/attachments/cancel',{id:task.asset}).catch(()=>{});
  if(attachmentTask===task){attachmentTask=null;state.filePreparing=false;$('#attachment-preview').hidden=true;}
}
async function chooseLargeAttachment(file,kind){
  await cancelAttachmentTask();
  const task={file,kind,cid:state.active,asset:null,cancelled:false};
  attachmentTask=task;state.filePreparing=true;state.attachment=null;state.fileAttachment=null;
  $('#attachment-preview').hidden=false;
  async function run(){
    $('#attachment-preview').innerHTML=`<span class="file-icon">${icons('copy')}</span><span class="transfer-detail"><b>${escape(file.name)}</b><small id="upload-label">正在准备加密附件…</small><progress id="upload-progress" max="${Math.max(1,file.size)}" value="0"></progress></span><button id="cancel-upload" aria-label="取消上传">×</button>`;
    $('#cancel-upload').onclick=()=>cancelAttachmentTask();
    try{
      const start=await post('/desktop/attachments/start',{name:file.name.slice(0,180),size:file.size,kind,resume:task.asset});
      task.asset=start.id;
      for(let index=0;index<start.parts;index++){
        if(task.cancelled||task.cid!==state.active)throw new Error('上传已取消。');
        const part=file.slice(index*start.chunk_size,Math.min(file.size,(index+1)*start.chunk_size));
        for(let attempt=0;;attempt++){
          task.controller=new AbortController();
          const timer=setTimeout(()=>task.controller.abort(),90000);
          try{
            const response=await fetch(`/desktop/attachments/chunk?asset=${encodeURIComponent(task.asset)}&index=${index}`,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Weiyan-Client':'web'},body:part,signal:task.controller.signal});
            const result=await response.json();if(!response.ok)throw new Error(result.error||'附件传输未完成。');
            break;
          }catch(error){if(task.cancelled||attempt>=2)throw error;await new Promise(r=>setTimeout(r,1000*(attempt+1)));}finally{clearTimeout(timer);}
        }
        const bytes=Math.min(file.size,(index+1)*start.chunk_size);
        if($('#upload-progress')){$('#upload-progress').value=bytes;$('#upload-label').textContent=`加密上传 ${fileSize(bytes)} / ${fileSize(file.size)} · ${Math.floor((index+1)*100/start.parts)}%`;}
      }
      if(task.cancelled)throw new Error('上传已取消。');
      const result=await post('/desktop/attachments/finish',{id:task.asset});
      if(task.cancelled||task.cid!==state.active)return;
      state.filePreparing=false;attachmentTask=null;previewAttachment(result,kind==='video');
    }catch(error){
      if(task.cancelled||task.cid!==state.active){state.filePreparing=false;return;}
      $('#attachment-preview').innerHTML=`<span class="transfer-detail"><b>${escape(file.name)}</b><small>${escape(error.message||'上传已暂停。')} 已加密保存，可重试继续。</small></span><button id="retry-upload">重试</button><button id="cancel-upload" aria-label="取消上传">×</button>`;
      $('#retry-upload').onclick=()=>{task.done=run();};$('#cancel-upload').onclick=()=>cancelAttachmentTask();
    }
  }
  task.done=run();await task.done;
}
async function chooseAttachment(file,asFile=false){
  if(!file)return;
  if(file.size>MAX_ATTACHMENT_SIZE){toast('请选择 10 GB 以内的图片、视频或文件。');return;}
  if(attachmentTask)await cancelAttachmentTask();
  if(!asFile&&file.type.startsWith('image/')){state.fileAttachment=null;return chooseImage(file);}
  const video=!asFile&&(/\.(mp4|webm)$/i.test(file.name)||['video/mp4','video/webm'].includes(file.type));
  if(file.size>20*1024**2)return chooseLargeAttachment(file,video?'video':'file');
  const cid=state.active,control=$('#file-button');control.disabled=true;
  try{
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
    const result=await post('/api/attachments',{data,name:file.name.slice(0,180),kind:video?'video':'file'});
    if(cid!==state.active)return;
    previewAttachment(result,video);
  }catch(e){toast(e.message||'文件没有读取完成，请重试。');}finally{control.disabled=false;$('#file-input').value='';$('#video-input').value='';}
}
$('#image-button').insertAdjacentHTML('afterend',`<button class="icon-button" id="video-button" title="发送视频" aria-label="发送视频"><svg viewBox="0 0 24 24"><rect x="3" y="5" width="13" height="14" rx="3"/><path d="m16 10 5-3v10l-5-3z"/></svg></button><button class="icon-button" id="file-button" title="发送文件" aria-label="发送文件">${icons('copy')}</button>`);
$('#composer').insertAdjacentHTML('beforeend','<input type="file" id="file-input" hidden><input type="file" id="video-input" accept="video/mp4,video/webm" hidden>');
$('#file-button').onclick=()=>$('#file-input').click();$('#video-button').onclick=()=>$('#video-input').click();
$('#file-input').onchange=()=>chooseAttachment($('#file-input').files[0],true);
$('#video-input').onchange=()=>chooseAttachment($('#video-input').files[0]);
const originalChooseImage=chooseImage;
chooseImage=async function(file){
  if(!file)return;
  if(file.size>MAX_ATTACHMENT_SIZE){toast('请选择 10 GB 以内的图片。');return;}
  if(attachmentTask)await cancelAttachmentTask();
  state.fileAttachment=null;
  if(file.size>5*1024**2)return chooseLargeAttachment(file,'image');
  return originalChooseImage(file);
};
const originalSelectConversation=selectConversation;
selectConversation=async function(...args){if(attachmentTask)await cancelAttachmentTask();return originalSelectConversation(...args);};
let checkingAttachments=false;
setInterval(async()=>{
  if(checkingAttachments||!state.me)return;
  const nodes=$$('[data-asset][data-ready="false"]');if(!nodes.length)return;
  checkingAttachments=true;
  try{
    const ids=[...new Set(nodes.map(node=>node.dataset.asset))].slice(0,100);
    const states=await api('/desktop/attachments/status?ids='+ids.join(','));
    for(const node of nodes){
      const status=states[node.dataset.asset];if(!status||!node.isConnected)continue;
      attachmentStates.set(node.dataset.asset,status);
      if(status.ready){node.innerHTML=attachmentContent(JSON.parse(node.dataset.attachment),true);node.dataset.ready='true';}
      else{const label=$('[data-transfer-progress]',node);if(label)label.textContent=status.missing?'附件已移除':`加密接收 ${fileSize(status.received||0)} / ${fileSize(status.size||0)} · 网络恢复后继续`;}
    }
  }catch{}finally{checkingAttachments=false;}
},1500);
let dragDepth=0;
document.addEventListener('dragover',e=>{if(e.dataTransfer?.types.includes('Files'))e.preventDefault();});
document.addEventListener('drop',e=>{if(e.dataTransfer?.files.length)e.preventDefault();});
$('#composer').addEventListener('dragenter',e=>{if(e.dataTransfer?.types.includes('Files')){e.preventDefault();dragDepth++;$('#composer').classList.add('file-drop-target');}});
$('#composer').addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('#composer').classList.remove('file-drop-target');}});
$('#composer').addEventListener('drop',e=>{e.preventDefault();dragDepth=0;$('#composer').classList.remove('file-drop-target');if(e.dataTransfer.files.length){chooseAttachment(e.dataTransfer.files[0]);if(e.dataTransfer.files.length>1)toast('每条消息可添加一个附件，请依次发送。');}});
