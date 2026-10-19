'use strict';
const messageCache=new Map();
const observedMessages=new Set();
function messageIdentity(m){return JSON.stringify([m.conversation_id,m.sender.id,m.client_id||m.id]);}
function observeMessage(m){const key=messageIdentity(m),seen=observedMessages.has(key);observedMessages.add(key);while(observedMessages.size>1000)observedMessages.delete(observedMessages.values().next().value);return seen;}
function cacheCurrentMessages(){
  if(!state.active)return;
  messageCache.delete(state.active);messageCache.set(state.active,{messages:new Map(state.messages),hasMore:state.hasMore});
  while(messageCache.size>20)messageCache.delete(messageCache.keys().next().value);
}
// Ranges in the editor use UTF-16; the encrypted protocol uses Unicode code points.
function mentionsMe(message){return (message.mentions||[]).some(m=>m.user_id===state.me?.id);}
function messageExcerpt(m){return m.body||(m.attachment?({image:'[图片]',video:'[视频]',file:'[文件]'}[m.attachment.kind]||'[文件]')+' '+m.attachment.name:m.image?'[图片]':'');}
function makeQuote(m){return {id:m.id,client_id:m.client_id,sender_id:m.sender.id,sender_name:m.sender.nickname,excerpt:Array.from(messageExcerpt(m)).slice(0,160).join(''),kind:m.attachment?.kind||(m.image?'image':'text')};}
function currentQuote(q){
  const source=state.messages.get(q.id);
  if(source?.recalled&&source.client_id===q.client_id&&source.sender.id===q.sender_id)return {...q,recalled:true,excerpt:'消息已撤回'};
  return q;
}
function quoteHTML(reference){
  if(!reference)return '';
  const q=currentQuote(reference);
  return `<button class="message-quote" data-quote="${q.id}" aria-label="查看引用的原消息"><b>${escape(q.sender_name)}</b><span>${escape(q.recalled?'消息已撤回':q.excerpt)}</span></button>`;
}
function renderMessageText(m){
  if(!m.mentions?.length)return messageBody(m.body);
  const chars=Array.from(m.body);let end=0,html='';
  for(const mention of m.mentions){
    html+=messageBody(chars.slice(end,mention.start).join(''));
    html+=`<button class="message-mention${mention.user_id===state.me?.id?' mentions-me':''}" data-user="${escape(mention.user_id)}">${escape(chars.slice(mention.start,mention.start+mention.length).join(''))}</button>`;
    end=mention.start+mention.length;
  }
  return html+messageBody(chars.slice(end).join(''));
}
function renderReplyPreview(){
  const preview=$('#reply-preview');preview.hidden=!state.reply;if(!state.reply)return;
  const q=currentQuote(state.reply);state.reply=q;
  preview.innerHTML=`<b>回复 ${escape(q.sender_name)}</b>${escape(q.recalled?'消息已撤回':q.excerpt)}<button class="icon-button" id="cancel-reply" aria-label="取消回复">${icons('close')}</button>`;
  $('#cancel-reply').onclick=()=>{state.reply=null;preview.hidden=true;saveDraft();};
}
function setMessageQuote(message){state.reply=makeQuote(message);renderReplyPreview();saveDraft();$('#message-input').focus();}
function scrubUIQuotes(message){
  const scrub=q=>q?.id===message.id&&q.client_id===message.client_id&&q.sender_id===message.sender.id?{...q,excerpt:'消息已撤回',recalled:true}:q;
  for(const item of state.messages.values())item.quote=scrub(item.quote);
  for(const cache of messageCache.values())for(const item of cache.messages.values())item.quote=scrub(item.quote);
  for(const item of state.outbox)item.quote=scrub(item.quote);
  const drafts=stored('drafts');for(const draft of Object.values(drafts))if(draft&&typeof draft==='object')draft.quote=scrub(draft.quote);
  const saved=stored('saved',[]);for(const item of saved)item.quote=scrub(item.quote);
  state.reply=scrub(state.reply);renderReplyPreview();store('drafts',drafts);store('outbox',state.outbox);store('saved',saved);
}
function resetMentionInput(){state.mentions=[];state.mentionText='';closeMentionPicker();}
function restoreComposerDraft(draft){
  resetMentionInput();const input=$('#message-input');
  input.value=typeof draft==='string'?draft:(draft?.body||'');
  state.mentions=typeof draft==='object'&&Array.isArray(draft?.mentions)?draft.mentions:[];
  state.mentionText=input.value;state.reply=typeof draft==='object'?draft?.quote||null:null;renderReplyPreview();
}
function trackMentionEdits(){
  const text=$('#message-input').value,old=state.mentionText||'';
  if(text===old)return;
  let left=0,right=0;
  while(left<old.length&&left<text.length&&old[left]===text[left])left++;
  while(right<old.length-left&&right<text.length-left&&old[old.length-1-right]===text[text.length-1-right])right++;
  const oldEnd=old.length-right,delta=text.length-old.length;
  state.mentions=(state.mentions||[]).flatMap(m=>{
    if(m.end<=left)return [m];
    if(m.start>=oldEnd)return [{...m,start:m.start+delta,end:m.end+delta}];
    return [];
  }).filter(m=>text.slice(m.start,m.end)===m.text);
  state.mentionText=text;
}
function outgoingMentions(){
  trackMentionEdits();const body=$('#message-input').value,offset=body.length-body.trimStart().length,trim=body.trim();
  return (state.mentions||[]).filter(m=>m.start>=offset&&m.end<=offset+trim.length).map(m=>({user_id:m.user_id,start:Array.from(body.slice(offset,m.start)).length,length:Array.from(m.text).length}));
}
const mentionPicker=document.createElement('div');mentionPicker.className='mention-picker';mentionPicker.id='mention-picker';mentionPicker.hidden=true;mentionPicker.setAttribute('role','listbox');mentionPicker.setAttribute('aria-label','选择要 @ 的成员');$('#composer').append(mentionPicker);
const mentionButton=document.createElement('button');mentionButton.className='icon-button';mentionButton.id='mention-button';mentionButton.textContent='@';mentionButton.title='提及成员';mentionButton.setAttribute('aria-label','提及成员');$('.toolbar-left').append(mentionButton);
let mentionTimer,mentionSequence=0,mentionAnchor=null,mentionResults=[],mentionIndex=0;
function closeMentionPicker(){clearTimeout(mentionTimer);mentionSequence++;mentionPicker.hidden=true;mentionAnchor=null;$('#message-input').removeAttribute('aria-activedescendant');}
function mentionQuery(){
  const input=$('#message-input'),head=input.value.slice(0,input.selectionStart),match=head.match(/(?:^|[\s，。！？；：、（(])@([^@\n]{0,40})$/);
  return match?{start:head.length-match[1].length-1,end:input.selectionStart,query:match[1]}:null;
}
function chooseMention(user){
  const anchor=mentionAnchor;if(!anchor)return;const input=$('#message-input');
  const token='@'+(user.group_nickname||user.nickname||user.username),text=token+' ';
  input.setRangeText(text,anchor.start,anchor.end,'end');trackMentionEdits();
  state.mentions.push({user_id:user.id,start:anchor.start,end:anchor.start+token.length,text:token});state.mentions.sort((a,b)=>a.start-b.start);
  closeMentionPicker();updateInput();input.focus();
}
async function findMentions(){
  const anchor=mentionQuery();if(!anchor||!state.active){closeMentionPicker();return;}
  const cid=state.active,seq=++mentionSequence;mentionAnchor=anchor;
  mentionResults=[];
  mentionPicker.hidden=false;mentionPicker.innerHTML='<p>正在查找会话成员…</p>';
  try{
    const result=await api('/api/participants?conversation_id='+encodeURIComponent(cid)+'&q='+encodeURIComponent(anchor.query));
    if(seq!==mentionSequence||cid!==state.active)return;
    mentionResults=result.users.filter(u=>u.id!==state.me.id);mentionIndex=0;
    mentionResults.forEach(u=>state.users.set(u.id,u));
    mentionPicker.innerHTML='<div class="mention-picker-heading">选择成员 · @ 提醒不受会话免打扰限制</div>'+(mentionResults.map((u,i)=>`<button id="mention-option-${i}" role="option" aria-selected="${i===0}" data-mention-index="${i}">${avatar(u,true)}<span><b>${escape(u.group_nickname||u.nickname)}</b><small>@${escape(u.username)}</small></span></button>`).join('')||'<p>没有找到会话成员</p>');
    $$('[data-mention-index]',mentionPicker).forEach(b=>b.onclick=()=>chooseMention(mentionResults[Number(b.dataset.mentionIndex)]));
  }catch(error){if(seq===mentionSequence)mentionPicker.innerHTML=`<p>${escape(error.message)}</p>`;}
}
mentionButton.onclick=()=>{const input=$('#message-input'),prefix=input.selectionStart&&!/\s$/.test(input.value.slice(0,input.selectionStart))?' @':'@';input.setRangeText(prefix,input.selectionStart,input.selectionEnd,'end');updateInput();input.focus();findMentions();};
$('#message-input').addEventListener('input',()=>{clearTimeout(mentionTimer);if(!mentionQuery()){closeMentionPicker();return;}mentionTimer=setTimeout(findMentions,160);});
$('#message-input').addEventListener('keydown',event=>{
  if(mentionPicker.hidden||event.isComposing||event.keyCode===229)return;
  if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();closeMentionPicker();return;}
  if(!mentionResults.length)return;
  if(['ArrowUp','ArrowDown','Enter','Tab'].includes(event.key)){
    event.preventDefault();event.stopImmediatePropagation();
    if(event.key==='Enter'||event.key==='Tab'){chooseMention(mentionResults[mentionIndex]);return;}
    mentionIndex=(mentionIndex+(event.key==='ArrowDown'?1:-1)+mentionResults.length)%mentionResults.length;
    $$('[role="option"]',mentionPicker).forEach((b,i)=>b.setAttribute('aria-selected',String(i===mentionIndex)));
    $('#message-input').setAttribute('aria-activedescendant','mention-option-'+mentionIndex);$('#mention-option-'+mentionIndex)?.scrollIntoView({block:'nearest'});
  }
},true);
document.addEventListener('pointerdown',e=>{if(!mentionPicker.contains(e.target)&&e.target!==$('#message-input')&&e.target!==mentionButton)closeMentionPicker();});
async function openQuotedMessage(message){
  const q=currentQuote(message.quote),cid=message.conversation_id;
  if(q.recalled){toast('消息已撤回。');return;}
  const existing=state.messages.get(q.id),element=$(`[data-message="${q.id}"]`,$('#messages'));
  if(existing?.client_id===q.client_id&&existing.sender.id===q.sender_id&&element){element.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});element.classList.remove('highlight');requestAnimationFrame(()=>element.classList.add('highlight'));return;}
  try{
    const result=await api(`/api/conversations/${encodeURIComponent(cid)}/messages?local=1&message_id=${q.id}`),m=result.message;
    if(!m||m.client_id!==q.client_id||m.sender.id!==q.sender_id){toast('原消息尚未保存在本机。');return;}
    if(m.recalled){toast('消息已撤回。');return;}
    modal('引用的原消息',`<div class="quoted-original"><b>${escape(m.sender.nickname)}</b><small>${escape(dateLabel(m.created_at)+' '+clock(m.created_at))}</small>${m.image?`<img class="message-image" src="${escape(m.image)}" alt="引用的图片">`:''}${m.attachment?attachmentHTML(m.attachment):''}<p class="message-text">${renderMessageText(m)}</p></div>`);
  }catch(error){toast(error.message);}
}
$('#messages').addEventListener('click',event=>{const button=event.target.closest('[data-quote]');if(!button)return;const m=state.messages.get(Number(button.closest('[data-message]').dataset.message));if(m?.quote)openQuotedMessage(m);});
