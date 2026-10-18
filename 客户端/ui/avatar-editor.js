'use strict';

// One editor for personal and group avatars; original files stay on this device.
function createAvatarEditor(root,{value,color,onChange}) {
  let current=value,currentColor=color,bitmap=null,zoom=1,offset={x:0,y:0},drag=null,generation=0,loading=false;
  root.classList.add('avatar-editor');
  root.innerHTML=`<div class="avatar-upload-row"><button type="button" class="avatar-current" aria-label="选择头像图片">${avatar({avatar:value,color})}</button><div><button type="button" class="avatar-file-button">选择图片</button><p>支持 PNG、JPEG、WebP，原图不超过 10 MB</p></div><input type="file" class="avatar-file" accept="image/png,image/jpeg,image/webp" hidden></div><div class="avatar-crop" hidden><canvas width="256" height="256" aria-label="拖动图片调整头像位置"></canvas><p>拖动图片调整位置，滑动下方控件缩放。</p><label>缩放<input class="avatar-zoom" type="range" min="1" max="4" step="0.01" value="1" aria-label="头像缩放"></label><div class="avatar-pan"><button type="button" data-pan="left" aria-label="向左移动">←</button><button type="button" data-pan="up" aria-label="向上移动">↑</button><button type="button" data-pan="down" aria-label="向下移动">↓</button><button type="button" data-pan="right" aria-label="向右移动">→</button></div><div class="avatar-crop-actions"><button type="button" class="avatar-cancel">取消裁剪</button><button type="button" class="primary avatar-apply">使用此头像</button></div></div><p class="avatar-error form-error" role="alert"></p>`;
  const find=s=>root.querySelector(s),file=find('.avatar-file'),crop=find('.avatar-crop'),canvas=find('canvas'),ctx=canvas.getContext('2d'),error=find('.avatar-error');
  function preview(){find('.avatar-current').innerHTML=avatar({avatar:current,color:currentColor});}
  function dispose(){generation++;bitmap?.close?.();bitmap=null;drag=null;loading=false;crop.hidden=true;file.value='';}
  function draw(){
    if(!bitmap)return;
    const scale=Math.max(256/bitmap.width,256/bitmap.height)*zoom,w=bitmap.width*scale,h=bitmap.height*scale;
    offset.x=Math.max(-(w-256)/2,Math.min((w-256)/2,offset.x));offset.y=Math.max(-(h-256)/2,Math.min((h-256)/2,offset.y));
    ctx.fillStyle='#fff8fa';ctx.fillRect(0,0,256,256);ctx.drawImage(bitmap,(256-w)/2+offset.x,(256-h)/2+offset.y,w,h);
  }
  function setAvatar(next){dispose();current=next;preview();error.textContent='';}
  find('.avatar-current').onclick=find('.avatar-file-button').onclick=()=>file.click();
  file.onchange=async()=>{
    const chosen=file.files[0];if(!chosen)return;
    if(chosen.size>10*1024*1024){error.textContent='请选择 10 MB 以内的头像图片。';file.value='';return;}
    if(!['image/png','image/jpeg','image/webp'].includes(chosen.type)){error.textContent='请选择 PNG、JPEG 或 WebP 图片。';file.value='';return;}
    const request=++generation;loading=true;error.textContent='';
    try{
      const loaded=await createImageBitmap(chosen,{imageOrientation:'from-image'});
      if(request!==generation||!root.isConnected){loaded.close();return;}
      if(loaded.width*loaded.height>40000000||loaded.width<16||loaded.height<16){loaded.close();throw new Error('请选择 16 像素以上、4000 万像素以内的图片。');}
      bitmap?.close?.();bitmap=loaded;zoom=1;offset={x:0,y:0};find('.avatar-zoom').value='1';crop.hidden=false;draw();canvas.scrollIntoView({block:'nearest',behavior:'smooth'});
    }catch(e){if(request===generation)error.textContent=e.message.startsWith('请选择')?e.message:'无法读取这张图片，请重新选择。';}
    finally{if(request===generation){file.value='';loading=false;}}
  };
  canvas.onpointerdown=e=>{if(!bitmap)return;canvas.setPointerCapture(e.pointerId);drag={x:e.clientX,y:e.clientY,ox:offset.x,oy:offset.y};};
  canvas.onpointermove=e=>{if(!drag)return;const ratio=256/canvas.getBoundingClientRect().width;offset={x:drag.ox+(e.clientX-drag.x)*ratio,y:drag.oy+(e.clientY-drag.y)*ratio};draw();};
  canvas.onpointerup=canvas.onpointercancel=()=>{drag=null;};
  find('.avatar-zoom').oninput=e=>{zoom=Number(e.target.value);draw();};
  root.querySelectorAll('[data-pan]').forEach(b=>b.onclick=()=>{const d=b.dataset.pan;offset.x+=(d==='right'?8:d==='left'?-8:0);offset.y+=(d==='down'?8:d==='up'?-8:0);draw();});
  find('.avatar-cancel').onclick=()=>{dispose();error.textContent='';};
  find('.avatar-apply').onclick=()=>{
    if(!bitmap)return;
    current=canvas.toDataURL('image/jpeg',.88);dispose();preview();error.textContent='';onChange(current);
  };
  const dialog=root.closest('dialog');
  dialog?.addEventListener('close',dispose);
  const observer=new MutationObserver(()=>{if(!root.isConnected){dispose();observer.disconnect();dialog?.removeEventListener('close',dispose);}});
  observer.observe(root.parentElement,{childList:true});
  return {setAvatar,setColor(next){currentColor=next;preview();},hasPending(){const pending=loading||!crop.hidden;if(pending)error.textContent=loading?'正在读取图片，请稍候。':'请先使用此头像，或取消裁剪。';return pending;},destroy:dispose};
}
