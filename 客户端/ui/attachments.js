'use strict';
function fileSize(bytes){return bytes>=1048576?(bytes/1048576).toFixed(1)+' MB':bytes>=1024?(bytes/1024).toFixed(1)+' KB':bytes+' B';}
function attachmentHTML(file){
  if(!file)return '';
  const url=escape(file.url),name=escape(file.name);
  return file.kind==='video'?`<div class="video-attachment"><video class="message-video" src="${url}" controls preload="metadata" playsinline aria-label="${name}"></video><a class="file-caption" href="${url}?download=1" download="${name}">${name} · ${fileSize(file.size)} ↓</a></div>`:`<a class="file-attachment" href="${url}?download=1" download="${name}"><span class="file-icon">${icons('copy')}</span><span><b>${name}</b><small>${fileSize(file.size)} · 保存文件</small></span><span>↓</span></a>`;
}
async function chooseAttachment(file,asFile=false){
  if(!file)return;
  if(!asFile&&file.type.startsWith('image/')){state.fileAttachment=null;return chooseImage(file);}
  if(file.size>20*1024*1024){toast('请选择 20 MB 以内的文件或视频。');return;}
  const video=!asFile&&(/\.(mp4|webm)$/i.test(file.name)||['video/mp4','video/webm'].includes(file.type));
  const cid=state.active;const control=$('#file-button');control.disabled=true;
  try{
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
    const result=await post('/api/attachments',{data,name:file.name.slice(0,180),kind:video?'video':'file'});
    if(cid!==state.active)return;
    state.attachment=null;state.fileAttachment=result;
    $('#attachment-preview').innerHTML=`${video?`<video class="attachment-video-preview" src="${escape(result.url)}" controls preload="metadata" playsinline></video>`:`<span class="file-icon">${icons('copy')}</span>`}<span><b>${escape(result.name)}</b><small>${fileSize(result.size)} · ${video?'视频预览':'文件准备就绪'}</small></span><button id="cancel-file" aria-label="移除附件">×</button>`;
    $('#attachment-preview').hidden=false;
    $('#cancel-file').onclick=()=>{state.fileAttachment=null;$('#attachment-preview').hidden=true;$('#attachment-preview').innerHTML='';};
  }catch(e){toast(e.message||'文件没有读取完成，请重试。');}finally{control.disabled=false;$('#file-input').value='';$('#video-input').value='';}
}
$('#image-button').insertAdjacentHTML('afterend',`<button class="icon-button" id="video-button" title="发送视频" aria-label="发送视频"><svg viewBox="0 0 24 24"><rect x="3" y="5" width="13" height="14" rx="3"/><path d="m16 10 5-3v10l-5-3z"/></svg></button><button class="icon-button" id="file-button" title="发送文件" aria-label="发送文件">${icons('copy')}</button>`);
$('#composer').insertAdjacentHTML('beforeend','<input type="file" id="file-input" hidden><input type="file" id="video-input" accept="video/mp4,video/webm" hidden>');
$('#file-button').onclick=()=>$('#file-input').click();$('#video-button').onclick=()=>$('#video-input').click();
$('#file-input').onchange=()=>chooseAttachment($('#file-input').files[0],true);
$('#video-input').onchange=()=>chooseAttachment($('#video-input').files[0]);
const originalChooseImage=chooseImage;
chooseImage=async function(file){if(file)state.fileAttachment=null;return originalChooseImage(file);};
let dragDepth=0;
document.addEventListener('dragover',e=>{if(e.dataTransfer?.types.includes('Files'))e.preventDefault();});
document.addEventListener('drop',e=>{if(e.dataTransfer?.files.length)e.preventDefault();});
$('#composer').addEventListener('dragenter',e=>{if(e.dataTransfer?.types.includes('Files')){e.preventDefault();dragDepth++;$('#composer').classList.add('file-drop-target');}});
$('#composer').addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('#composer').classList.remove('file-drop-target');}});
$('#composer').addEventListener('drop',e=>{e.preventDefault();dragDepth=0;$('#composer').classList.remove('file-drop-target');if(e.dataTransfer.files.length){chooseAttachment(e.dataTransfer.files[0]);if(e.dataTransfer.files.length>1)toast('每条消息可添加一个附件，请依次发送。');}});
