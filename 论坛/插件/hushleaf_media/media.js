(() => {
  'use strict';
  if (window.hushleafMediaInstalled) return;
  window.hushleafMediaInstalled = true;
  const labels = {video: '视频', audio: '音频', web: '网页'};
  let active = null;
  const dialog = document.createElement('dialog');
  dialog.className = 'hl-media-dialog';
  dialog.innerHTML = '<form method="dialog" class="hl-media-form">' +
    '<div class="hl-media-heading"><h2>插入媒体</h2><button type="button" class="hl-media-close" aria-label="关闭">×</button></div>' +
    '<label>链接<input class="hl-media-url" type="url" placeholder="https://" autocomplete="off"></label>' +
    '<label class="hl-media-local">或上传本地文件<input class="hl-media-file" type="file"><small>单个附件最大 1 GB，仍受当前账号和板块权限限制。</small></label>' +
    '<div class="hl-media-dimensions"><label>宽度<input class="hl-media-width" value="100%" required placeholder="100% 或 720"></label>' +
    '<label>高度（像素）<input class="hl-media-height" type="number" min="40" max="1920" required value="405"></label></div>' +
    '<p class="hl-media-help">宽度支持 1%–100% 或 120–1920 像素；手机端自动适应屏幕。网页能否嵌入取决于对方网站设置。</p>' +
    '<p class="hl-media-status" role="status" aria-live="polite"></p><div class="hl-media-actions"><button type="button" class="hl-media-cancel">取消</button>' +
    '<button type="submit" class="hl-media-submit">插入</button></div></form>';
  document.body.appendChild(dialog);
  const find = s => dialog.querySelector(s);
  const status = find('.hl-media-status'), submit = find('.hl-media-submit'), file = find('.hl-media-file');
  let busy = false;
  function close() { if (!busy) dialog.close(); }
  find('.hl-media-close').onclick = close;
  find('.hl-media-cancel').onclick = close;
  dialog.addEventListener('cancel', e => { if (busy) e.preventDefault(); });
  dialog.addEventListener('click', e => { if (e.target === dialog) close(); });
  file.addEventListener('change', () => { if (file.files.length) find('.hl-media-url').value = ''; });
  find('.hl-media-url').addEventListener('input', () => { file.value = ''; });

  function open(textarea, type) {
    active = {textarea, type, start: textarea.selectionStart, end: textarea.selectionEnd,
      blockIndex: window.editor?.blocks?.getCurrentBlockIndex?.()};
    find('h2').textContent = '插入' + labels[type];
    find('.hl-media-url').value = ''; file.value = ''; status.textContent = '';
    find('.hl-media-local').hidden = type === 'web';
    file.accept = type === 'video' ? '.mp4,.webm,.ogv,.mov,.m4v' : '.mp3,.m4a,.ogg,.wav,.aac,.flac,.opus';
    find('.hl-media-width').value = '100%';
    find('.hl-media-height').value = type === 'video' ? '405' : type === 'audio' ? '54' : '480';
    dialog.showModal();
    find('.hl-media-url').focus();
  }

  async function upload(selected, textarea) {
    if (selected.size > 1073741824) throw new Error('文件超过 1 GB，请选择较小的文件。');
    const allowed = active.type === 'video' ? /\.(mp4|webm|ogv|mov|m4v)$/i : /\.(mp3|m4a|ogg|wav|aac|flac|opus)$/i;
    if (!allowed.test(selected.name)) throw new Error('请选择与媒体类型相符的音视频文件。');
    const form = textarea.form;
    if (!form) throw new Error('请在完整发帖或回复页面上传文件。');
    const formhash = form.querySelector('[name="formhash"]')?.value || window.FORMHASH || '';
    const fid = new URL(location.href).searchParams.get('fid') || window.fid || window.fidstr || form.querySelector('[name="fid"]')?.value;
    if (!/^\d+$/.test(String(fid))) throw new Error('请进入板块的完整发帖或回复页面后上传。');
    const configResponse = await fetch('plugin.php?id=hushleaf_media:uploadconfig&formhash=' + encodeURIComponent(formhash), {credentials:'same-origin',cache:'no-store'});
    const config = await configResponse.json();
    if (!configResponse.ok) throw new Error(config.error || '无法获取上传权限，请重新登录。');
    if (selected.size > config.maximum) throw new Error('文件超过当前用户组的附件上限。');
    const body = new FormData();body.append('uid',config.uid);body.append('hash',config.hash);body.append('Filedata',selected);
    const response = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST','misc.php?mod=swfupload&operation=upload&simple=1&fid=' + encodeURIComponent(fid));
      xhr.upload.onprogress = event => { if (event.lengthComputable) status.textContent = '正在上传 ' + Math.round(event.loaded/event.total*100) + '%…'; };
      xhr.onload = () => xhr.status === 200 ? resolve(xhr.responseText) : reject(new Error('上传失败，请稍后重试。'));
      xhr.onerror = () => reject(new Error('网络中断，未完成上传。'));
      xhr.ontimeout = () => reject(new Error('上传超时，请重试。'));
      xhr.timeout = 3600000;xhr.send(body);
    });
    const match = /DISCUZUPLOAD\|0\|(\d+)\|/.exec(response);
    if (!match) throw new Error('论坛拒绝了该附件，请检查板块权限、文件类型及大小。');
    const aid = match[1];
    const input = document.createElement('input');input.type='hidden';input.name='attachnew[' + aid + '][description]';input.value='';form.appendChild(input);
    return 'attach:' + aid;
  }

  find('form').addEventListener('submit', async event => {
    event.preventDefault();if (busy || !active) return;
    let width = find('.hl-media-width').value.trim();
    const height = Number(find('.hl-media-height').value);
    const pixels = /^\d+$/.test(width) && Number(width)>=120 && Number(width)<=1920;
    const percentage = /^(?:[1-9][0-9]?|100)%$/.test(width);
    if ((!pixels && !percentage) || !Number.isInteger(height) || height<40 || height>1920) {status.textContent='请填写有效的宽度和高度。';return;}
    if (pixels) width = String(Number(width));
    let source = find('.hl-media-url').value.trim();
    const selected = active.type !== 'web' && file.files[0];
    if (!selected) {
      try { const url = new URL(source);if(url.protocol!=='https:' || url.username || url.password || /[\s<>"'\\\[\]]/.test(source)) throw new Error(); }
      catch {status.textContent='请填写 HTTPS 链接，或选择本地音视频文件。';return;}
    }
    busy=true;submit.disabled=true;find('.hl-media-close').disabled=true;find('.hl-media-cancel').disabled=true;
    try {
      if (selected) source = await upload(selected, active.textarea);
      const {textarea,type,start,end}=active;
      const tag='\n[hl'+type+'='+width+','+height+']'+source+'[/hl'+type+']\n';
      if (textarea.tagName === 'INPUT' && window.editor?.blocks?.insert) {
        await window.editor.isReady;
        const text = tag.trim().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        const index = Number.isInteger(active.blockIndex) && active.blockIndex >= 0 ? active.blockIndex + 1 : window.editor.blocks.getBlocksCount();
        window.editor.blocks.insert('paragraph',{text},undefined,index,true);
      } else if (textarea.id === String(window.editorid) + '_textarea' && typeof window.insertText === 'function') {
        window.insertText(tag);
      } else {
        textarea.focus();textarea.setSelectionRange(start,end);textarea.setRangeText(tag,start,end,'end');
        textarea.dispatchEvent(new Event('input',{bubbles:true}));textarea.dispatchEvent(new Event('change',{bubbles:true}));
      }
      dialog.close();textarea.focus();
    } catch (error) {status.textContent=error.message || '操作未完成，请重试。';}
    finally {busy=false;submit.disabled=false;find('.hl-media-close').disabled=false;find('.hl-media-cancel').disabled=false;}
  });

  function mount() {
    document.querySelectorAll('textarea[name="message"],textarea#fastpostmessage,.json-editor input[name="message"]').forEach(textarea => {
      if (textarea.dataset.hlMedia) return;
      textarea.dataset.hlMedia='1';
      const toolbar=document.createElement('div');toolbar.className='hl-media-toolbar';toolbar.setAttribute('aria-label','插入媒体');
      Object.entries(labels).forEach(([type,label]) => {const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>open(textarea,type);toolbar.appendChild(button);});
      const editor = textarea.id === String(window.editorid) + '_textarea' && document.getElementById(window.editorid + '_controls');
      if (editor) editor.before(toolbar);
      else if (textarea.tagName === 'INPUT') textarea.closest('.json-editor').before(toolbar);
      else textarea.before(toolbar);
    });
  }
  mount();
  let queued=false;
  new MutationObserver(() => {if(!queued){queued=true;requestAnimationFrame(()=>{queued=false;mount();});}}).observe(document.body,{childList:true,subtree:true});
})();
