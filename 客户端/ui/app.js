'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const icons = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const animals = { bunny:'🐰', cat:'🐱', bear:'🐻', flower:'🌼', moon:'🌙', sparkles:'✨' };
const state = {me:null, conversations:[], contacts:[], online:[], users:new Map(), active:null, messages:new Map(), outbox:[], hasMore:false, view:'messages', filter:'all', authTab:'login', stream:null, connected:false, attachment:null, reply:null, sending:false, server:'', switching:0, loadingOlder:false};
let toastTimer, searchTimer, typingTimer, typingSent=0, syncTimer, scrollTimer, attemptedAuth=false, authGeneration=0;
function avatar(user, small = false, online = false) { return `<span class="avatar${small ? ' small' : ''}" style="background:${escape(user.color || '#ffe4ec')}">${/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(user.avatar || '') ? `<img class="custom-avatar" src="${escape(user.avatar)}" alt="头像">` : animals[user.avatar] || '🌼'}${online ? '<span class="online-dot"></span>' : ''}</span>`; }
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 3400); }
function storageKey(name) { return `weiyan:${state.server}:${state.me?.id || 'guest'}:${name}`; }
let localPreferences={},storageWrites=Promise.resolve();
function stored(name,fallback={}) {return localPreferences[name]??fallback;}
function store(name,value) {
  localPreferences[name]=value;
  const body=JSON.stringify({values:{[name]:value}});
  storageWrites=storageWrites.catch(()=>{}).then(()=>fetch('/desktop/storage',{method:'POST',credentials:'same-origin',keepalive:body.length<60000,headers:{'Content-Type':'application/json','X-Weiyan-Client':'web'},body})).then(r=>{if(!r.ok)throw new Error('本机加密保存失败');return true;}).catch(()=>{toast('本机保存尚未完成，请保持窗口开启后重试。');return false;});
  return storageWrites;
}
function conversationNotificationRow(cid) {
  const muted=stored('muted',[]).includes(cid);
  return `<div class="settings-row"><div>消息免打扰<p>开启后仍接收消息并保留未读标记，普通消息不发出通知或托盘提醒；@ 你的消息仍会提醒。仅对本机当前账号生效。</p></div><button class="notification-toggle${muted?' on':''}" id="conversation-mute" role="switch" aria-label="消息免打扰" aria-checked="${muted}" data-mute-conversation="${escape(cid)}"></button></div>`;
}
async function setConversationMuted(cid,enabled) {
  const before=[...stored('muted',[])];
  const next=enabled?[...new Set([...before,cid])]:before.filter(x=>x!==cid);
  if(!await store('muted',next)){localPreferences.muted=before;return false;}
  renderList();
  toast(enabled?'已开启免打扰，未读标记仍会保留。':'已开启提醒。');
  return true;
}
function bindConversationMute() {
  const button=$('#conversation-mute');
  if(!button)return;
  button.onclick=async()=>{
    button.disabled=true;
    const cid=button.dataset.muteConversation,enabled=!stored('muted',[]).includes(cid);
    if(await setConversationMuted(cid,enabled)){button.classList.toggle('on',enabled);button.setAttribute('aria-checked',String(enabled));}
    button.disabled=false;
  };
}
async function migrateLocalPreferences(data) {
  localPreferences=data.local_storage||{};
  const values={},prefix=storageKey('');
  for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);if(!key.startsWith(prefix))continue;const name=key.slice(prefix.length);if(!['drafts','outbox','saved','pins','muted','notifications','lastConversation'].includes(name))continue;try{if(!(name in localPreferences))values[name]=JSON.parse(localStorage.getItem(key));}catch{}}
  if(Object.keys(values).length){await post('/desktop/storage',{values});Object.assign(localPreferences,values);}
  for(const key of Object.keys(localStorage))if(key.startsWith(prefix))localStorage.removeItem(key);
}
function currentConversation() { return state.conversations.find(c => c.id === state.active); }
function clock(value) { return new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)); }
function dateLabel(value) { const date = new Date(value), today = new Date(); const yesterday = new Date(today); yesterday.setDate(today.getDate()-1); if (date.toDateString() === today.toDateString()) return '今天'; if (date.toDateString() === yesterday.toDateString()) return '昨天'; return `${date.getMonth()+1}月${date.getDate()}日`; }
function shortTime(value) { return dateLabel(value) === '今天' ? clock(value) : dateLabel(value); }
async function api(path, options = {}) {
  const response = await fetch(path, {credentials:'same-origin', ...options, headers:{'Content-Type':'application/json','X-Weiyan-Client':'web',...options.headers}});
  let data; try { data = await response.json(); } catch { throw new Error('暂时没有收到回应，请稍后再试。'); }
  if (!response.ok) { const error = new Error(data.error || '操作没有完成，请重试。'); error.status=response.status; error.offline=data.offline; throw error; }
  return data;
}
function post(path, data = {}) { return api(path,{method:'POST',body:JSON.stringify(data)}); }
function setConnection(connected, message) {
  state.connected = connected;
  $('#connection-dot').classList.toggle('offline', !connected);
  $('#connection-text').textContent = message || (connected ? '小笺已连接 · 安心畅聊' : '连接中断，正在重连…');
}
function rememberUsers() {
  [state.me,...state.contacts,...state.conversations.map(c => c.peer).filter(Boolean)].forEach(u => state.users.set(u.id,u));
}
async function bootstrap({initial=false,refreshMessages=false}={}) {
  const generation=authGeneration;
  if(state.signingOut)return;
  const data = await api('/api/bootstrap');
  if(generation!==authGeneration || state.signingOut)return;
  state.me=data.me; state.conversations=data.conversations; state.contacts=data.contacts; state.online=data.online;
  state.groupInvites=data.group_invites||[];
  state.friendRequests=data.friend_requests||[];state.outgoingFriendRequests=data.outgoing_friend_requests||[];
  if(typeof renderGroupInvites==='function')renderGroupInvites();
  if(typeof renderFriendRequests==='function')renderFriendRequests();
  if(state.loadedUser!==state.me.id){await migrateLocalPreferences(data);state.loadedUser=state.me.id;state.outbox=stored('outbox',[]).map(m=>({...m,status:'failed',inFlight:false}));}
  if(generation!==authGeneration || state.signingOut)return;
  (data.online_users || []).forEach(u => state.users.set(u.id,u)); rememberUsers();
  $('#auth').hidden=true; $('#app').hidden=false; $('#boot').hidden=true;
  $('#my-avatar').innerHTML=avatar(state.me); $('#my-avatar').setAttribute('aria-label',`我的资料：${state.me.nickname}`);
  renderList(); updateHeader(); renderOnline();
  if (initial || !state.active || !state.conversations.some(c=>c.id===state.active)) {
    const last=stored('lastConversation', 'room:lobby');
    await selectConversation(state.conversations.some(c => c.id===last) ? last : 'room:lobby');
  } else if (refreshMessages) await loadMessages(state.active, true);
}
function scheduleSync() { clearTimeout(syncTimer); syncTimer=setTimeout(() => bootstrap().catch(() => {}), 180); }
function connectEvents() {
  if(!state.me || state.signingOut)return;
  state.stream?.close();
  state.stream = new IMTransport(data => {
    if (!state.me) return;
    if(data.type==='expired'){state.stream.close();showAuth('登录已过期，请重新登录。');return;}
    if (data.type==='message') receiveMessage(data.message);
    if (data.type==='security') {$('#encryption-info').classList.add('key-warning');$('#encryption-info').title='设备或安全校验需要确认，点击查看';}
    if (data.type==='devices') {$('#encryption-info').title='设备目录已更新，点击查看安全指纹';}
    if (data.type==='presence') { state.online=data.online; updateHeader(); renderOnline(); scheduleSync(); }
    if (['conversations','profile','resync'].includes(data.type)) bootstrap({refreshMessages:data.type!=='conversations'}).catch(() => {});
    if (data.type==='read') { const conv=state.conversations.find(c=>c.id===data.conversation_id); if(conv){if(data.user_id===state.me.id){conv.unread=0;conv.mention_unread=0;}else conv.peer_last_read=data.last_id;} renderList();if(data.conversation_id===state.active && data.user_id!==state.me.id)renderMessages(); }
    if (data.type==='typing' && data.conversation_id===state.active && data.user_id!==state.me.id) {
      $('#chat-status').textContent=`${data.nickname} 正在写小笺…`; clearTimeout(typingTimer); typingTimer=setTimeout(updateHeader,3500);
    }
  }, connected => {setConnection(connected);if(connected)bootstrap({refreshMessages:true}).then(()=>{for(const message of state.outbox.filter(m=>m.status==='failed' && (m.autoRetries||0)<3)){message.autoRetries=(message.autoRetries||0)+1;transmitOutgoing(message);}}).catch(()=>{});});
}
function showAuth(error='') {
  authGeneration++;clearTimeout(syncTimer);state.signingOut=false;
  messageCache.clear();observedMessages.clear();resetMentionInput();
  state.stream?.close(); state.stream=null; state.me=null; state.active=null; state.messages.clear(); state.loadedUser=null;localPreferences={};
  $('#app').hidden=true; $('#auth').hidden=false; $('#boot').hidden=true; $('#auth-error').textContent=error;
}
function updateHeader() {
  const conv=currentConversation(); if(!conv) return;
  $('#chat-title').textContent=conv.name; $('#chat-avatar').innerHTML=avatar(conv);
  const online=conv.kind==='direct' ? state.online.includes(conv.peer.id) : state.online.length>0;
  const label=conv.kind==='direct' ? (online ? '在线 · 此刻和你一起' : '暂时离线 · 小笺会好好保存') : conv.kind==='group' ? `群聊 · ${conv.group.member_count} 位成员` : `公共聊天室 · ${state.online.length} 位朋友在线`;
  $('#chat-status').innerHTML=`<span class="status-dot${online?'':' offline'}"></span>${label}`;
  $('#room-description').textContent=conv.description; $('#room-notice').hidden=conv.kind!=='room' && !conv.group?.announcement;
  $('#room-notice>span').textContent=conv.kind==='group' ? '群公告：'+conv.group.announcement : '交流日常，分享文字与见闻。';
  $('#online-heading').textContent=conv.kind==='direct'?'关于这位朋友':'此刻在这里';
  if(typeof updateStrangerBanner==='function')updateStrangerBanner();
}
function renderOnline() {
  if(!state.me) return;
  const conv=currentConversation();
  const users=conv?.kind==='direct' ? [conv.peer] : state.online.map(id=>state.users.get(id)).filter(Boolean);
  $('#online-count').textContent=conv?.kind==='direct'?'':`${state.online.length} 位`;
  $('#online-avatars').innerHTML=users.slice(0,5).map(user=>`<button data-user="${escape(user.id)}" title="${escape(user.nickname)}">${avatar(user,true)}</button>`).join('');
  $('#online-note').textContent=conv?.kind==='direct' ? (conv.peer.bio || '还没写下签名，先聊聊吧。') : '以真诚相待，以文字相伴。';
}
function renderList() {
  if(!state.me) return;
  const q=$('#conversation-search').value.trim().toLowerCase(), list=$('#conversation-list');
  const pins=stored('pins', ['room:lobby']);
  const muted=stored('muted', []);
  $('#sidebar-title').innerHTML=({messages:'我的小客厅', contacts:'我的朋友们',saved:'收藏的小笺'}[state.view])+'<span>✿</span>';
  $('#list-tabs').hidden=state.view!=='messages';
  $('#conversation-search').placeholder=state.view==='contacts'?'搜索朋友的名字':state.view==='saved'?'搜索收藏的小笺':'搜索会话或朋友';
  $$('.rail-button[data-view]').forEach(b=>b.classList.toggle('active', b.dataset.view===state.view));
  if(state.view==='contacts') {
    const contacts=state.contacts.filter(u=>`${u.nickname} ${u.username}`.toLowerCase().includes(q));
    list.innerHTML=`<div class="list-section-title">我的好友 <span>${contacts.length} 位</span></div>`+contacts.map(user=>`<button class="conversation-item" data-peer="${user.id}">${avatar(user,false,state.online.includes(user.id))}<div class="conversation-copy"><div class="conversation-name">${escape(user.nickname)}</div><div class="conversation-preview">${escape(user.bio || '@'+user.username)}</div></div></button>`).join('')+(contacts.length?'':emptyHTML('🌷','还没有找到朋友','点击右上角 +，添加第一位朋友吧。'));
    return;
  }
  if(state.view==='saved') {
    const saved=stored('saved',[]).filter(m=>`${m.body} ${m.sender.nickname}`.toLowerCase().includes(q));
    list.innerHTML=saved.map(m=>`<button class="saved-result" data-jump="${m.id}" data-conversation="${m.conversation_id}"><b>${escape(m.sender.nickname)}</b><p>${escape((m.body || '[图片]').slice(0,120))}</p><small>${dateLabel(m.created_at)} ${clock(m.created_at)}</small></button>`).join('')+(saved.length?'':emptyHTML('🎀','把喜欢的句子收藏起来','在消息上点击收藏，就能在这里找到。'));
    return;
  }
  let convs=state.conversations.filter(c=>c.name.toLowerCase().includes(q) || (c.last_message?.body || '').toLowerCase().includes(q));
  if(state.filter==='unread') convs=convs.filter(c=>c.unread>0);
  convs.sort((a,b)=>(Number(pins.includes(b.id))-Number(pins.includes(a.id))) || ((b.last_message?.id||0)-(a.last_message?.id||0)));
  const total=state.conversations.reduce((sum,c)=>sum+(muted.includes(c.id)?(c.mention_unread||0):c.unread),0);
  $('#total-unread').hidden=!total; $('#total-unread').textContent=total>99?'99+':total;
  document.title=total?`(${total}) 未言诗集`:'未言诗集 · 每一句，都被温柔听见';
  list.innerHTML=convs.map(c=>{
    const m=c.last_message;
    const prefix=m && m.sender.id!==state.me.id && c.kind!=='direct' ? m.sender.nickname+'：' : '';
    return `<button class="conversation-item${c.id===state.active?' active':''}" data-conv="${escape(c.id)}">${avatar(c,false,c.kind==='direct'&&state.online.includes(c.peer.id))}<div class="conversation-copy"><div class="conversation-line"><span class="conversation-name">${escape(c.name)}${pins.includes(c.id)?'<svg class="conversation-pin"><use href="#i-pin"/></svg>':''}</span><span class="conversation-time">${m?shortTime(m.created_at):''}</span></div><div class="conversation-line"><span class="conversation-preview">${muted.includes(c.id)?'🔕 ':''}${c.mention_unread?'<b class="mention-unread">[有人@我] </b>':''}${escape(prefix+(m?(m.body||(m.attachment?(m.attachment.kind==='video'?'[视频]':m.attachment.kind==='image'?'[图片]':'[文件]')+' '+m.attachment.name:'[图片]')):'还没有小笺，打个招呼吧'))}</span>${c.unread?`<span class="unread-badge">${c.unread>99?'99+':c.unread}</span>`:''}</div></div></button>`;
  }).join('')+(convs.length?'':emptyHTML('☁️','这里暂时安安静静的',state.filter==='unread'?'所有小笺都已经读过啦。':'换个名字搜索试试吧。'));
}
function emptyHTML(icon,title,description) { return `<div class="empty-state"><span class="empty-icon">${icon}</span>${escape(title)}<br><small>${escape(description)}</small></div>`; }
function saveDraft() { if(!state.active || !state.me) return; const drafts=stored('drafts'); drafts[state.active]={body:$('#message-input').value,quote:state.reply,mentions:state.mentions||[]}; store('drafts',drafts); }
async function selectConversation(cid) {
  if(!state.conversations.some(c=>c.id===cid)) await bootstrap();
  if(!state.conversations.some(c=>c.id===cid)) return;
  saveDraft();cacheCurrentMessages();state.active=cid; state.switching++;const cached=messageCache.get(cid);state.messages=new Map(cached?.messages||[]);state.hasMore=cached?.hasMore||false;state.reply=null; state.attachment=null; state.fileAttachment=null; state.pendingClient=null;
  $('#app').classList.add('mobile-chat'); $('#reply-preview').hidden=true; $('#attachment-preview').hidden=true; $('#emoji-picker').hidden=true; $('#new-message-button').hidden=true;
  restoreComposerDraft(stored('drafts')[cid]); updateInput();
  store('lastConversation',cid); updateHeader(); renderOnline(); renderList();
  renderMessages();scrollBottom();
  try { await loadMessages(cid); } catch(error) { if(state.active===cid)toast(error.message); }
}
async function loadMessages(cid, preserve=false) {
  const generation=state.switching;
  const data=await api(`/api/conversations/${encodeURIComponent(cid)}/messages?local=1`);
  if(cid!==state.active || generation!==state.switching) return;
  const nearBottom=isNearBottom();
  state.messages=new Map([...state.messages,...data.messages.map(m=>[m.id,m])]); state.hasMore=data.has_more;
  const confirmed=new Set(data.messages.filter(m=>m.sender.id===state.me.id).map(m=>m.client_id));
  state.outbox=state.outbox.filter(m=>!confirmed.has(m.client_id));store('outbox',state.outbox);
  data.messages.forEach(m=>{if(!state.users.has(m.sender.id))state.users.set(m.sender.id,m.sender);});
  rememberUsers();
  renderMessages(); if(!preserve || nearBottom) scrollBottom();
  cacheCurrentMessages();
  const ids=[...new Set(data.messages.slice(-100).map(m=>m.sender.id))];
  if(ids.length)api('/api/profiles?conversation_id='+encodeURIComponent(cid)+'&ids='+encodeURIComponent(ids.join(','))).then(profiles=>{if(cid!==state.active||generation!==state.switching)return;profiles.users.forEach(u=>state.users.set(u.id,u));renderMessages();}).catch(()=>{});
  markRead(); renderOnline();
}
function messageBody(body) {
  return String(body).split(/(https?:\/\/[^\s<>]+)/g).map(part=>/^https?:\/\//.test(part)?`<a href="${escape(part)}" target="_blank" rel="noopener noreferrer">${escape(part)}</a>`:escape(part)).join('');
}
function renderMessages() {
  const items=[...state.messages.values(),...state.outbox.filter(m=>m.conversation_id===state.active)].sort((a,b)=>new Date(a.created_at)-new Date(b.created_at) || ((Number(a.id)||0)-(Number(b.id)||0))), container=$('#messages');
  $('#load-older').hidden=!state.hasMore;
  if(!items.length) { container.innerHTML='<div class="chat-empty"><img src="/assets/letter-garden.svg" alt="信封里的兔兔"><h3>从一句「你好」开始吧</h3><p>每一段温柔的故事，<br>都值得一封小笺。</p></div>'; return; }
  let day='',previousTime=0;
  container.innerHTML=items.map(m=>{
    const own=m.sender.id===state.me.id,stamp=new Date(m.created_at).getTime(); const label=dateLabel(m.created_at); const divider=label!==day||stamp-previousTime>120000?`<div class="message-date">${label} · ${clock(m.created_at)}</div>`:''; day=label;previousTime=stamp;
    if(m.recalled)return `${divider}<div class="message-date recalled-message" data-message="${m.id}">${own?'你撤回了一条消息':'消息已撤回'}</div>`;
    const read=currentConversation()?.kind==='direct' && currentConversation().peer_last_read>=m.id;
    const delivery=m.status==='sending'?'<span class="send-state">寄送中…</span>':m.status==='failed'?`<button class="retry-send" data-retry="${escape(m.client_id)}">发送失败 · 重试</button>`:own?`<span title="${read?'对方已读':'已加密发送；离线密文投递后删除'}">${icons('check')}${read?' 已读':''}</span>`:'';
    return `${divider}<article class="message${own?' mine':''}" data-message="${m.id}" tabindex="0"><button class="message-avatar" data-user="${m.sender.id}" title="${escape(m.sender.nickname)}" style="padding:0">${avatar(state.users.get(m.sender.id)||m.sender,true)}</button><div class="message-content"><div class="sender-name">${escape(own?'我':m.sender.nickname)}${m.sender.id==='system'?'<span class="sender-label">小客厅管家</span>':''}</div><div class="message-bubble">${quoteHTML(m.quote)}${m.image?`<img class="message-image" src="${escape(m.image)}" alt="${escape(m.sender.nickname)}分享的图片" loading="lazy">`:''}${m.attachment?attachmentHTML(m.attachment):''}${m.body?`<div class="message-text">${renderMessageText(m)}</div>`:''}</div><div class="message-time">${clock(m.created_at)}${delivery}</div></div>${m.status?'':`<div class="message-actions"><button data-action="reply" title="回复">${icons('chat')}</button><button data-action="copy" title="复制">${icons('copy')}</button><button data-action="save" title="收藏">${icons('bookmark')}</button><button data-action="more" title="更多">${icons('more')}</button></div>`}</article>`;
  }).join('');
}
function isNearBottom() { const el=$('#message-scroll'); return el.scrollHeight-el.scrollTop-el.clientHeight<140; }
function scrollBottom() { const el=$('#message-scroll'); el.scrollTop=el.scrollHeight; $('#new-message-button').hidden=true; }
function markRead() {
  if(!state.active || !state.me || document.hidden) return;
  if(innerWidth<=620&&!$('#app').classList.contains('mobile-chat'))return;
  const ids=[...state.messages.keys()]; if(!ids.length) return;
  const last=Math.max(...ids), cid=state.active;
  post('/api/read',{conversation_id:cid,last_id:last}).then(()=>{const c=state.conversations.find(c=>c.id===cid);if(c){c.unread=0;c.mention_unread=0;renderList();}}).catch(()=>{});
}
function receiveMessage(m) {
  const duplicate=observeMessage(m)||state.messages.has(m.id)||messageCache.get(m.conversation_id)?.messages.has(m.id)||state.conversations.some(c=>c.last_message?.id===m.id);
  const cached=messageCache.get(m.conversation_id);if(cached)cached.messages.set(m.id,m);
  if(m.recalled || m.history_sync){
    const conv=state.conversations.find(c=>c.id===m.conversation_id);
    if(conv&&(!conv.last_message||conv.last_message.id<=m.id))conv.last_message=m;
    if(m.recalled){scrubUIQuotes(m);const saved=stored('saved',[]).filter(s=>s.id!==m.id);store('saved',saved);if(state.reply?.id===m.id){state.reply=null;$('#reply-preview').hidden=true;}}
    if(m.conversation_id===state.active){state.messages.set(m.id,m);renderMessages();}
    renderList();return;
  }
  if(m.sender.id===state.me.id && state.outbox.some(p=>p.client_id===m.client_id)){state.outbox=state.outbox.filter(p=>p.client_id!==m.client_id);store('outbox',state.outbox);}
  state.users.set(m.sender.id,m.sender);
  const conv=state.conversations.find(c=>c.id===m.conversation_id);
  if(!conv) { scheduleSync(); return; }
  if(duplicate){if(m.conversation_id===state.active){state.messages.set(m.id,m);renderMessages();}return;}
  conv.last_message=m;
  const visible=m.conversation_id===state.active && !document.hidden && (innerWidth>620||$('#app').classList.contains('mobile-chat'));
  if(m.sender.id!==state.me.id && !visible) {conv.unread++;if(mentionsMe(m))conv.mention_unread=(conv.mention_unread||0)+1;notify(m,conv);}
  if(m.conversation_id===state.active) {
    const bottom=isNearBottom(); state.messages.set(m.id,m); renderMessages();
    if(bottom || m.sender.id===state.me.id) scrollBottom(); else $('#new-message-button').hidden=false;
    if(visible) markRead();
  }
  renderList();
}
function notify(m,conv) {
  if(state.native)return;
  if(stored('muted',[]).includes(conv.id) && !mentionsMe(m)) return;
  if(stored('notifications',false) && 'Notification' in window && Notification.permission==='granted' && document.hidden) {
    try { const n=new Notification(conv.name,{body:'收到一条已加密消息，打开客户端查看。',icon:'/assets/logo.svg',tag:conv.id});n.onclick=()=>{window.focus();selectConversation(conv.id);n.close();}; } catch { /* The unread badge still notifies. */ }
  }
}
function updateInput() { trackMentionEdits(); $('#character-count').textContent=`${$('#message-input').value.length} / 2000`; saveDraft(); }
async function sendMessage() {
  if(state.sending || !state.active) return;
  if(state.filePreparing){toast('附件正在加密上传，请准备完成后发送。');return;}
  let body=$('#message-input').value.trim();
  if(!body && !state.attachment && !state.fileAttachment) {$('#message-input').focus(); return;}
  if(body.length>2000) {toast('消息连同回复最多 2000 个字符，请稍微缩短一点。');return;}
  const client_id=crypto.randomUUID?.() || Date.now()+'-'+Math.random().toString(16).slice(2);
  const outgoing={id:'pending-'+client_id,conversation_id:state.active,body,image_id:state.attachment?.id,image:state.attachment?.url||null,client_id,created_at:new Date().toISOString(),sender:{...state.me},status:'sending'};
  outgoing.quote=state.reply;outgoing.mentions=outgoingMentions();
  if(state.fileAttachment){outgoing.file_id=state.fileAttachment.id;outgoing.attachment={...state.fileAttachment};}
  state.outbox.push(outgoing);store('outbox',state.outbox);
  resetMentionInput();
  $('#message-input').value='';state.attachment=null;state.fileAttachment=null;state.reply=null;$('#attachment-preview').hidden=true;$('#reply-preview').hidden=true;updateInput();renderMessages();scrollBottom();$('#message-input').focus();
  await transmitOutgoing(outgoing);
}
async function transmitOutgoing(outgoing) {
  if(outgoing.inFlight)return;outgoing.inFlight=true;outgoing.status='sending';if(outgoing.conversation_id===state.active)renderMessages();
  try {if(!state.stream)throw new Error('正在连接服务端，稍后点击重试即可。');const message=await state.stream.send({conversation_id:outgoing.conversation_id,body:outgoing.body,image_id:outgoing.image_id,file_id:outgoing.file_id,client_id:outgoing.client_id,quote:outgoing.quote,mentions:outgoing.mentions});receiveMessage(message);}
  catch(error){if(state.outbox.some(m=>m.client_id===outgoing.client_id)){outgoing.status='failed';store('outbox',state.outbox);if(outgoing.conversation_id===state.active)renderMessages();toast(error.message);}}
  finally{outgoing.inFlight=false;store('outbox',state.outbox);}
}
async function openDirect(peerId) {
  try { const data=await post('/api/conversations',{peer_id:peerId}); await bootstrap(); state.view='messages'; closeModal(); await selectConversation(data.conversation.id); } catch(error){toast(error.message);}
}
function modal(title,content,extra='') { $('#modal-title').textContent=title; $('#modal-body').innerHTML=content; $('#modal').className='modal '+extra; WeiyanMotion.openDialog($('#modal')); }
function closeModal(){ clearTimeout(searchTimer); return WeiyanMotion.closeDialog($('#modal')); }
async function openSecurity(sync=false) {
  modal('加密与隐私','<p>正在读取本机加密信息…</p>');
  try {
    const cid=sync===true?'sync:'+state.me.id:state.active;
    const info=await api('/desktop/security'+(cid?'?conversation_id='+encodeURIComponent(cid):''));
    if($('#modal-title').textContent!=='加密与隐私')return;
    $('#encryption-info').classList.toggle('key-warning',info.key_changed);
    $('#modal-body').innerHTML=`<div class="security-intro">${icons('lock')}<div><h3>你的交流，已启用端到端加密</h3><p>消息离开本机前加密，仅会话成员的设备可解密。</p></div></div><div class="security-rules"><div><b>文字、图片、视频与文件</b><p>使用 libsodium 加密，并校验发送设备签名。服务端没有消息解密私钥。</p></div><div><b>本机聊天记录</b><p>在当前设备加密保存，Windows 使用账号保护密钥，安卓使用系统密钥库。核对设备安全指纹后，可从同账号已登录设备自动同步记录。</p></div><div><b>离线密文</b><p>未送达的密文永久保留，不设队列总容量限制；设备确认接收后删除。</p></div><div><b>会话范围</b><p>${currentConversation()?.kind==='room'?'本公开聊天室面向全部已注册成员；发送时登记的成员设备均可解密。':'私聊和群聊仅向当前会话成员已登记的设备加密分发。'}服务端仍可见账号、设备、会话成员与投递元数据。</p></div></div><details class="security-fingerprints" ${info.key_changed?'open':''}><summary>核对设备安全指纹${info.key_changed?' · 发现设备变化':''}</summary><p>首次联系会记住设备公钥。请通过当面或其他可信渠道核对指纹；新增或更换设备需确认。</p><div class="fingerprint-own"><b>此设备</b><code>${escape(info.fingerprint)}</code></div>${info.devices.map(d=>`<div class="fingerprint-row${d.changed?' changed':''}"><b>${escape(d.nickname)} · ${escape(d.name)}${d.changed?' · 需要确认':''}</b><small>设备 ${escape(d.device_id.slice(0,12))}</small><code>${escape(d.fingerprint)}</code></div>`).join('')}${info.key_changed?'<div class="info-box">设备公钥发生变化，向这些设备发送和接收新消息已暂停。请核对指纹后再信任。</div><button class="primary" id="approve-devices">已核对，信任当前设备</button>':''}</details><p class="security-limit">本项目加密协议尚未经独立安全审计，不具备 Signal 双棘轮的前向保密能力。请保管好设备、系统账号与本机资料。</p>`;
    const deviceSync=document.createElement('div');deviceSync.className='info-box';deviceSync.innerHTML='<b>同账号设备同步</b><p>请在两台设备上核对安全指纹并确认信任。已登录设备会加密传送每个会话最近 50 条记录及附件，自己发出的新消息也会同步。至少一台保存记录的旧设备需要上线。</p><button id="show-sync-devices" class="text-button">核对我的设备</button>';$('#modal-body').append(deviceSync);$('#show-sync-devices').onclick=()=>openSecurity(true);
    if($('#approve-devices'))$('#approve-devices').onclick=async()=>{try{await post('/desktop/security',{conversation_id:cid,approve:true,directory_digest:info.directory_digest});toast('设备信任已更新。');await openSecurity(sync);await bootstrap({refreshMessages:true});}catch(e){toast(e.message);}};
  }catch(e){$('#modal-body').innerHTML=`<p>${escape(e.message)}</p>`;}
}
async function openUpdates() {
  modal('软件更新',`<div class="client-update-brand"><img src="/assets/logo.svg" alt="未言诗集"><div><b>未言诗集</b><p>客户端 · <span id="update-current">2.1.3</span></p></div></div><div class="info-box">从项目 GitHub Releases 下载正式版本。安装前会校验发布签名与文件完整性。</div><p id="client-update-state">点击检查，查看是否有可用更新。</p><div id="client-update-details" hidden><h3 id="client-update-version"></h3><pre id="client-update-notes"></pre></div><p id="client-update-repo" class="muted"></p><p id="client-update-progress" class="info-box" hidden></p><div class="modal-actions"><button class="text-button" id="client-check-update">检查更新</button><button class="primary" id="client-install-update" hidden>下载并重启安装</button></div><p class="muted">安装后会重新打开客户端，本机加密记录与设置会保留。</p>`);
  const config=await api('/desktop/update/info');if(!$('#update-current'))return;$('#update-current').textContent=config.current;$('#client-update-repo').textContent=config.repository;
  $('#client-check-update').onclick=async()=>{const b=$('#client-check-update');b.disabled=true;$('#client-update-state').textContent='正在连接 GitHub 检查更新…';try{const info=await post('/desktop/update/check');if(!$('#client-update-state'))return;$('#client-update-state').textContent=info.message;$('#client-update-details').hidden=!info.latest;$('#client-update-version').textContent=info.latest?'版本 '+info.latest:'';$('#client-update-notes').textContent=info.notes||'';$('#client-install-update').hidden=!info.available;}catch(e){if($('#client-update-state'))$('#client-update-state').textContent=e.message;}finally{b.disabled=false;}};
  $('#client-install-update').onclick=async()=>{const b=$('#client-install-update');b.disabled=true;$('#client-update-progress').hidden=false;$('#client-update-progress').textContent='正在下载并验证更新，请保持窗口开启…';try{await storageWrites;await post('/desktop/update/install');
      const poll=setInterval(async()=>{if(!$('#client-update-progress')){clearInterval(poll);return;}try{const progress=await api('/desktop/update/status');$('#client-update-progress').textContent=progress.message+(progress.stage==='downloading'?' · '+progress.percent+'%':'');if(['error','installing'].includes(progress.stage)){clearInterval(poll);if(progress.stage==='error')b.disabled=false;}}catch{clearInterval(poll);}},800);}catch(e){$('#client-update-progress').textContent=e.message;b.disabled=false;}};
}
function openConnection() {
  modal('连接你的小客厅',`<p>发布版默认连接 156.225.28.175:23074，也可填写自建服务端地址。</p><form id="connection-form"><label>服务端地址<input id="server-url" value="${escape(state.server)}" placeholder="https://156.225.28.175:23074" required></label><div class="info-box">在这台电脑上运行服务端：<strong>http://127.0.0.1:23074</strong><br>在另一台电脑上运行：填写那台电脑显示的局域网地址。<br>两台电脑需要连接同一个 Wi-Fi 或局域网。</div><p class="form-error" id="connection-error"></p><div class="modal-actions"><button type="button" class="text-button" data-close>取消</button><button type="submit" class="primary">保存并连接</button></div></form>`);
  $('#connection-form').onsubmit=async e=>{
    e.preventDefault(); const submit=$('button[type="submit"]',e.target); submit.disabled=true;
    try {await post('/desktop/config',{server_url:$('#server-url').value});location.reload();}catch(error){$('#connection-error').textContent=error.message;submit.disabled=false;}
  };
}
function openFriends() {
  modal('和新朋友打个招呼',`<p>搜索朋友的账号或昵称，开启一段新的对话。</p><div class="modal-search">${icons('search')}<input id="friend-search" placeholder="输入账号或昵称" autocomplete="off"></div><div id="friend-results" class="modal-results">${emptyHTML('🐰','在这里遇见你的朋友','让朋友注册账号后，搜索账号名即可找到。')}</div>`);
  $('#friend-search').focus();
  $('#friend-search').oninput=()=>{clearTimeout(searchTimer);const q=$('#friend-search').value.trim(); searchTimer=setTimeout(async()=>{
    if(!q){$('#friend-results').innerHTML=emptyHTML('🌷','输入朋友的账号或昵称','访客可在个人资料中设置正式账号。');return;}
    try {const data=await api('/api/users?q='+encodeURIComponent(q));if(!$('#friend-results'))return;data.users.forEach(u=>state.users.set(u.id,u));$('#friend-results').innerHTML=data.users.map(u=>`<div class="user-result">${avatar(u)}<div><b>${escape(u.nickname)}</b><p>@${escape(u.username)}</p></div><button data-peer="${u.id}">打个招呼</button></div>`).join('') || emptyHTML('☁️','暂时没有找到这位朋友','确认账号名后，再试一次吧。');}catch(error){toast(error.message);}
  },280);};
}
function openProfile(user=state.me) {
  if(user.id!==state.me.id) {
    modal('联系人资料',`<div class="profile-heading">${avatar(user)}<div><h3>${escape(user.nickname)}</h3><p>@${escape(user.username || 'xiaojian')}</p></div></div><p>${escape(user.bio||'还没写下签名，先聊聊吧。')}</p>${user.id==='system'?'<div class="info-box">小笺是系统的欢迎账号，负责在公共聊天室留下欢迎语。</div>':`<div class="modal-actions"><button class="primary" data-peer="${user.id}">发消息</button></div>`}`);return;
  }
  let selectedAvatar=state.me.avatar,selectedColor=state.me.color;
  modal('个人资料',`<div class="profile-heading">${avatar(state.me)}<div><h3>${escape(state.me.nickname)}</h3><p>${state.me.is_guest?'访客身份':'@'+escape(state.me.username)}</p></div></div><form id="profile-form"><label>头像</label><div id="profile-avatar-editor"></div><p class="avatar-privacy">头像是公开资料，会由服务端保存并向其他用户展示。</p><div class="avatar-options">${Object.keys(animals).map(a=>`<button type="button" class="avatar-option${a===selectedAvatar?' selected':''}" data-avatar="${a}" aria-label="${a}">${avatar({avatar:a,color:selectedColor})}</button>`).join('')}</div><label>头像颜色</label><div class="color-options">${['#ffe4ec','#eae5ff','#e2f1e8','#fff0d6','#dfefff'].map(c=>`<button type="button" class="color-option${c===selectedColor?' selected':''}" style="background:${c}" data-color="${c}" aria-label="${c}"></button>`).join('')}</div><label>昵称<input id="profile-nickname" maxlength="16" value="${escape(state.me.nickname)}" required></label><label>个人签名<input id="profile-bio" maxlength="80" value="${escape(state.me.bio)}" placeholder="愿未尽之言，皆有回响。"></label>${state.me.is_guest?'<div class="info-box">访客退出后无法通过密码找回。设置账号和密码，就能保留身份。本机聊天记录仅保存在当前设备。</div><label>设置账号（选填）<input id="profile-username" minlength="3" maxlength="24" placeholder="3–24 位字母、数字或下划线" autocomplete="username"></label><label>设置密码<input id="profile-password" type="password" minlength="8" maxlength="128" placeholder="至少 8 位" autocomplete="new-password"></label>':''}<p id="profile-error" class="form-error"></p><div class="modal-actions"><button class="primary" type="submit">保存我的小档案</button></div></form>`);
  const avatarEditor=createAvatarEditor($('#profile-avatar-editor'),{value:selectedAvatar,color:selectedColor,onChange:next=>{selectedAvatar=next;$$('[data-avatar]').forEach(b=>b.classList.remove('selected'));}});
  $$('[data-avatar]').forEach(b=>b.onclick=()=>{selectedAvatar=b.dataset.avatar;avatarEditor.setAvatar(selectedAvatar);$$('.avatar-option').forEach(x=>x.classList.toggle('selected',x===b));});
  $$('.color-option').forEach(b=>b.onclick=()=>{selectedColor=b.dataset.color;avatarEditor.setColor(selectedColor);$$('.color-option').forEach(x=>x.classList.toggle('selected',x===b));$$('.avatar-option .avatar').forEach(a=>a.style.background=selectedColor);});
  $('#profile-form').onsubmit=async e=>{e.preventDefault();if(avatarEditor.hasPending()){toast('请先使用此头像，或取消裁剪。');return;}const button=$('button[type="submit"]',e.target);button.disabled=true;
    try {const payload={nickname:$('#profile-nickname').value,bio:$('#profile-bio').value,avatar:selectedAvatar,color:selectedColor};if(state.me.is_guest && $('#profile-username').value){payload.username=$('#profile-username').value;payload.password=$('#profile-password').value;}await post('/api/profile',payload);await bootstrap({refreshMessages:true});closeModal();toast('小档案已保存，今天也很可爱。');}catch(error){$('#profile-error').textContent=error.message;}finally{button.disabled=false;}
  };
}
function openSettings() {
  modal('设置',`<div class="settings-row"><div>我的小档案<p>昵称、头像与个人签名</p></div><button id="settings-profile">编辑资料</button></div><div class="settings-row"><div>服务端连接<p>${escape(state.server)}</p></div><button id="settings-connect">更改地址</button></div><div class="settings-row"><div>桌面消息通知<p>窗口在后台时，提醒新的小笺</p></div><button class="notification-toggle${stored('notifications',false)?' on':''}" id="notification-toggle" aria-label="切换桌面通知" aria-pressed="${stored('notifications',false)}"></button></div><div class="settings-row"><div>发送方式<p>Enter 发送，Shift + Enter 换行</p></div><span>⌨️</span></div><div class="settings-row"><div>加密与隐私<p>端到端加密 · 本机加密保存 · 安全指纹</p></div><button id="settings-security">查看详情</button></div><div class="settings-row"><div>软件更新<p>从 GitHub 获取经过签名验证的正式版本</p></div><button id="settings-updates">检查更新</button></div><div class="info-box">未言诗集 2.2<br>文字与图片在客户端加密。服务端仅暂存待投递密文，送达后删除。</div><div class="modal-actions"><button class="text-button" id="logout-button">退出当前账号</button><button class="primary" data-close>布置好啦</button></div>`);
  $('#settings-security').onclick=openSecurity;$('#settings-updates').onclick=openUpdates;
  $('#settings-profile').onclick=()=>openProfile(); $('#settings-connect').onclick=openConnection;
  $('#notification-toggle').onclick=async()=>{
    const enabling=!stored('notifications',false);
    if(enabling){try{if(!('Notification' in window)||await Notification.requestPermission()!=='granted'){toast('系统未允许桌面通知，会话未读提示仍会保留。');return;}}catch{toast('当前系统没有开放桌面通知。');return;}}
    store('notifications',enabling);$('#notification-toggle').classList.toggle('on',enabling);$('#notification-toggle').setAttribute('aria-pressed',String(enabling));
  };
  $('#logout-button').onclick=()=>{
    modal('退出登录',`<p>${state.me.is_guest?'你正在使用访客身份。退出后无法找回这个访客账号；可以先在个人资料中设置账号和密码。':'退出后聊天记录仍在本机加密保存，使用本机原系统账号重新登录后可继续查看。'}</p><div class="modal-actions">${state.me.is_guest?'<button class="text-button" id="keep-account">先保存账号</button>':'<button class="text-button" data-close>再待一会儿</button>'}<button class="primary" id="confirm-logout">确认退出</button></div>`);
    if($('#keep-account'))$('#keep-account').onclick=()=>openProfile();
    $('#confirm-logout').onclick=async()=>{
      const button=$('#confirm-logout');button.disabled=true;state.signingOut=true;authGeneration++;clearTimeout(syncTimer);
      state.stream?.close();state.stream=null;
      try{await storageWrites;await post('/api/logout');await closeModal();showAuth();$('#auth-password').value='';}
      catch(error){state.signingOut=false;button.disabled=false;toast(error.message);connectEvents();}
    };
  };
}
function openSearch() {
  modal('翻一翻聊天记录',`<div class="modal-search">${icons('search')}<input id="message-search" placeholder="搜索说过的话…" autocomplete="off"></div><div id="search-results" class="modal-results">${emptyHTML('💌','有些话，想再读一遍','仅搜索保存在这台电脑上的聊天记录。')}</div>`);
  $('#message-search').focus();
  $('#message-search').oninput=()=>{clearTimeout(searchTimer);const q=$('#message-search').value.trim();searchTimer=setTimeout(async()=>{
    if(!q)return;try{const data=await api('/api/search?q='+encodeURIComponent(q));if(!$('#search-results'))return;$('#search-results').innerHTML=data.messages.map(m=>`<button class="search-result" data-jump="${m.id}" data-conversation="${m.conversation_id}"><b>${escape(m.sender.nickname)} · ${escape(state.conversations.find(c=>c.id===m.conversation_id)?.name||'聊天')}</b><p>${escape(m.body)}</p><small>${dateLabel(m.created_at)} ${clock(m.created_at)}</small></button>`).join('')||emptyHTML('☁️','这句话还没有出现过','换个关键词再翻一翻吧。');}catch(error){toast(error.message);}
  },280);};
}
async function jumpToMessage(cid,id) {
  closeModal(); state.view='messages'; await selectConversation(cid);let turns=0;
  while(!state.messages.has(Number(id)) && state.hasMore && turns++<40) await loadOlder();
  const target=$(`[data-message="${id}"]`);if(target){target.scrollIntoView({block:'center'});target.classList.add('highlight');}else toast('这封小笺较早，请继续查看更早的消息。');
}
async function loadOlder() {
  if(state.loadingOlder || !state.hasMore || !state.messages.size) return;
  state.loadingOlder=true;const cid=state.active, generation=state.switching; $('#load-older').disabled=true;
  try {const before=Math.min(...state.messages.keys());const data=await api(`/api/conversations/${encodeURIComponent(cid)}/messages?before=${before}`);if(cid!==state.active||generation!==state.switching)return;const scroller=$('#message-scroll'), height=scroller.scrollHeight, top=scroller.scrollTop;data.messages.forEach(m=>state.messages.set(m.id,m));state.hasMore=data.has_more;renderMessages();scroller.scrollTop=top+scroller.scrollHeight-height;}catch(error){toast(error.message);}finally{state.loadingOlder=false;$('#load-older').disabled=false;}
}
function showContext(items,x,y) {
  const menu=$('#context-menu');menu.innerHTML=items.map((item,i)=>`<button data-menu="${i}">${icons(item.icon)}${escape(item.label)}</button>`).join('');menu.hidden=false;
  menu.style.left=Math.min(x,innerWidth-175)+'px';menu.style.top=Math.min(y,innerHeight-items.length*39-15)+'px';
  $$('button',menu).forEach((button,i)=>button.onclick=()=>{menu.hidden=true;items[i].action();});
}
function messageAction(action,id,event) {
  const m=state.messages.get(Number(id));if(!m)return;
  if(m.recalled)return;
  if(action==='recall'){post('/api/recall',{id:m.id,client_id:m.client_id}).then(r=>{receiveMessage(r.message);toast('消息已撤回。');}).catch(e=>toast(e.message));return;}
  if(action==='copy') { if(!m.body){toast('这是一封图片小笺。');return;} copyText(m.body); }
  if(action==='reply') setMessageQuote(m);
  if(action==='save'){let saved=stored('saved',[]);if(saved.some(s=>s.id===m.id)){saved=saved.filter(s=>s.id!==m.id);toast('已从收藏中取下。');}else{saved.unshift(m);toast('这句温柔已经收好啦。');}store('saved',saved);if(state.view==='saved')renderList();}
  if(action==='more'){const actions=[{icon:'chat',label:'回复这封小笺',action:()=>messageAction('reply',id)},{icon:'copy',label:'复制文字',action:()=>messageAction('copy',id)},{icon:'bookmark',label:'收藏 / 取消收藏',action:()=>messageAction('save',id)}];if(m.sender.id===state.me.id&&Date.now()-new Date(m.created_at).getTime()<120000)actions.push({icon:'close',label:'撤回消息',action:()=>messageAction('recall',id)});showContext(actions,event.clientX,event.clientY);}
}
async function copyText(text) {try{await navigator.clipboard.writeText(text);toast('已复制，可以分享这份温柔了。');}catch{const input=document.createElement('textarea');input.value=text;document.body.append(input);input.select();const ok=document.execCommand('copy');input.remove();toast(ok?'已复制。':'可以选中文字后，按 Ctrl+C 复制。');}}
function conversationContext(cid,event) {
  const pins=stored('pins',['room:lobby']),muted=stored('muted',[]);
  showContext([{icon:'pin',label:pins.includes(cid)?'取消置顶':'置顶这个会话',action:()=>{store('pins',pins.includes(cid)?pins.filter(x=>x!==cid):[...pins,cid]);renderList();}},{icon:'bell',label:muted.includes(cid)?'开启消息提醒':'消息免打扰',action:()=>setConversationMuted(cid,!stored('muted',[]).includes(cid))}],event.clientX,event.clientY);
}
async function chooseImage(file) {
  if(!file)return;if(!['image/png','image/jpeg','image/gif','image/webp'].includes(file.type)){toast('支持 PNG、JPG、GIF 和 WebP 图片。');return;}if(file.size>5*1024*1024){toast('图片需要小于 5 MB，换一张轻一点的吧。');return;}
  const cid=state.active;$('#image-button').disabled=true;
  try {const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});const result=await post('/api/upload',{data});if(cid!==state.active)return;state.attachment=result;$('#attachment-preview').innerHTML=`<img src="${escape(result.url)}" alt="待发送的图片"><span>图片准备好啦，点击寄出发送</span><button id="cancel-image" aria-label="移除图片">×</button>`;$('#attachment-preview').hidden=false;$('#cancel-image').onclick=()=>{state.attachment=null;$('#attachment-preview').hidden=true;};$('#message-input').focus();}catch(error){toast(error.message||'图片没有读出来，再试一次吧。');}finally{$('#image-button').disabled=false;$('#image-input').value='';}
}
// Event delegation keeps conversation changes and live updates predictable.
document.addEventListener('click',event=>{
  const retry=event.target.closest('[data-retry]');if(retry){const m=state.outbox.find(m=>m.client_id===retry.dataset.retry);if(m)transmitOutgoing(m);return;}
  const peer=event.target.closest('[data-peer]');if(peer){openDirect(peer.dataset.peer);return;}
  const conv=event.target.closest('[data-conv]');if(conv){selectConversation(conv.dataset.conv);return;}
  const userButton=event.target.closest('[data-user]');if(userButton){const u=state.users.get(userButton.dataset.user);if(u)openProfile(u);return;}
  const jump=event.target.closest('[data-jump]');if(jump){jumpToMessage(jump.dataset.conversation,jump.dataset.jump);return;}
  if(event.target.closest('[data-close]'))closeModal();
  const action=event.target.closest('[data-action]');if(action){messageAction(action.dataset.action,action.closest('[data-message]').dataset.message,event);return;}
  if(!event.target.closest('#context-menu'))$('#context-menu').hidden=true;
  if(!event.target.closest('#emoji-picker, #emoji-button'))$('#emoji-picker').hidden=true;
});
$('#conversation-list').addEventListener('contextmenu',event=>{const c=event.target.closest('[data-conv]');if(c){event.preventDefault();conversationContext(c.dataset.conv,event);}});
$('#messages').addEventListener('contextmenu',event=>{const m=event.target.closest('[data-message]');if(m && !window.getSelection().toString()){event.preventDefault();messageAction('more',m.dataset.message,event);}});
$('#messages').addEventListener('click',event=>{if(event.target.classList.contains('message-image'))modal('分享的小小日常',`<img src="${escape(event.target.src)}" alt="聊天图片">`,'image-lightbox');});
$('#message-scroll').addEventListener('load',event=>{if(event.target.tagName==='IMG' && isNearBottom())scrollBottom();},true);
$('#modal-close').onclick=closeModal;$('#modal').addEventListener('click',e=>{if(e.target===$('#modal')){const r=$('#modal').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeModal();}});
$$('[data-auth-tab]').forEach(button=>button.onclick=()=>{state.authTab=button.dataset.authTab;$$('[data-auth-tab]').forEach(b=>b.classList.toggle('active',b===button));const register=state.authTab==='register';$('#nickname-field').hidden=!register;$('#auth-nickname').required=register;$('#auth-password').minLength=register?8:1;$('#auth-password').autocomplete=register?'new-password':'current-password';$('#auth-title').textContent=register?'欢迎加入未言诗集':'欢迎回来，掇花者';$('#auth-subtitle').textContent=register?'创建账号，开启与彼此的交流。':'以文字相识，以交流相知。';$('#auth-submit-label').textContent=register?'开启我的小客厅':'进入小客厅';$('#auth-error').textContent='';});
$('#password-toggle').onclick=()=>{const input=$('#auth-password');input.type=input.type==='password'?'text':'password';$('#password-toggle').textContent=input.type==='password'?'显示':'隐藏';};
$('#auth-form').onsubmit=async event=>{event.preventDefault();const button=$('.auth-submit');button.disabled=true;$('#auth-error').textContent='';try{await post('/api/'+state.authTab,{username:$('#auth-username').value,password:$('#auth-password').value,nickname:$('#auth-nickname').value});await bootstrap({initial:true});connectEvents();}catch(error){$('#auth-error').textContent=error.message;}finally{button.disabled=false;}};
$('#guest-login').onclick=async()=>{$('#guest-login').disabled=true;try{await post('/api/guest',{nickname:'掇花者'});await bootstrap({initial:true});connectEvents();toast('欢迎来坐坐。点击左下角头像，可以设置自己的名字。');}catch(error){$('#auth-error').textContent=error.message;}finally{$('#guest-login').disabled=false;}};
$('#auth-updates').onclick=openUpdates;$('#auth-connect').onclick=openConnection;$('#new-chat').onclick=openFriends;$('#my-avatar').onclick=()=>openProfile();$('#settings-button').onclick=openSettings;$('#search-messages').onclick=openSearch;
$('#encryption-info').onclick=openSecurity;$('#privacy-card').onclick=openSecurity;
$('#conversation-info').onclick=()=>{const c=currentConversation();if(c.kind==='direct'){openProfile(c.peer);return;}modal(c.name,`<div class="profile-heading">${avatar(c)}<div><h3>${escape(c.name)}</h3><p>公共聊天室 · ${state.online.length} 位朋友在线</p></div></div><p>${escape(c.description)}</p><div class="info-box">所有连接到同一服务端的朋友都能参与这个聊天室。<br>友善地分享日常，也请尊重彼此的生活和隐私。</div>${conversationNotificationRow(c.id)}<h3 style="font-size:12px">此刻在这里</h3><div class="modal-results">${state.online.map(id=>state.users.get(id)).filter(Boolean).map(u=>`<div class="user-result">${avatar(u)}<div><b>${escape(u.nickname)}</b><p>${escape(u.bio||'愿未尽之言，皆有回响。')}</p></div>${u.id!==state.me.id?`<button data-peer="${u.id}">私聊</button>`:'<span style="font-size:10px;color:#b99daa">我</span>'}</div>`).join('')}</div>`);bindConversationMute();};
$$('[data-view]').forEach(button=>button.onclick=()=>{state.view=button.dataset.view;$('#conversation-search').value='';renderList();});
$$('[data-list]').forEach(button=>button.onclick=()=>{state.filter=button.dataset.list;$$('[data-list]').forEach(b=>b.classList.toggle('active',b===button));renderList();});
$('#conversation-search').oninput=renderList;$('#send-button').onclick=sendMessage;
$('#message-input').addEventListener('keydown',event=>{if(event.key==='Enter' && !event.shiftKey && !event.isComposing && event.keyCode!==229){event.preventDefault();sendMessage();}});
$('#message-input').addEventListener('input',()=>{updateInput();if(Date.now()-typingSent>3000 && $('#message-input').value.trim() && state.active){typingSent=Date.now();post('/api/typing',{conversation_id:state.active}).catch(()=>{});}});
$('#message-input').addEventListener('paste',event=>{const image=[...event.clipboardData.items].find(i=>i.type.startsWith('image/'));if(image){event.preventDefault();chooseImage(image.getAsFile());}});
$('#image-button').onclick=()=>$('#image-input').click();$('#image-input').onchange=()=>chooseImage($('#image-input').files[0]);
const emojis=['🌷','🐰','🌼','✨','☁️','💗','🌙','🥰','😊','😭','😂','🥺','😴','🤗','🎀','🫶','🍓','🍑','🍰','🧋','☕','💌','📖','🌸','🍀','🌈','⭐','🎉','🤍','💕','💐','🐱','🐻','🦋','🎵'];
$('#emoji-picker').innerHTML=emojis.map(e=>`<button aria-label="${e}">${e}</button>`).join('');
$('#emoji-button').onclick=()=>$('#emoji-picker').hidden=!$('#emoji-picker').hidden;
$$('#emoji-picker button').forEach(button=>button.onclick=()=>{const input=$('#message-input');if(input.value.length+button.textContent.length>2000)return;input.setRangeText(button.textContent,input.selectionStart,input.selectionEnd,'end');input.focus();updateInput();});
$('#poem-button').onclick=()=>{const input=$('#message-input');const text='总有温柔，值得慢慢收藏。\n不必急着抵达，沿途的花，也在等你呀。🌷';input.value+=(input.value?'\n':'')+text;input.value=input.value.slice(0,2000);input.focus();updateInput();};
$('#load-older').onclick=loadOlder;$('#new-message-button').onclick=()=>{scrollBottom();markRead();};$('#mobile-back').onclick=()=>$('#app').classList.remove('mobile-chat');
$('#message-scroll').addEventListener('scroll',()=>{if(isNearBottom())$('#new-message-button').hidden=true;clearTimeout(scrollTimer);scrollTimer=setTimeout(()=>{if(isNearBottom())markRead();},200);});
document.addEventListener('visibilitychange',()=>{if(!document.hidden && state.me){markRead();bootstrap({refreshMessages:true}).catch(()=>{});}});
document.addEventListener('keydown',event=>{if(event.ctrlKey && event.key.toLowerCase()==='k'){event.preventDefault();if(state.me)openSearch();}if(event.key==='Escape'){$('#context-menu').hidden=true;$('#emoji-picker').hidden=true;}});
window.addEventListener('beforeunload',saveDraft);
$('.rail-logo').onclick=event=>{event.preventDefault();state.view='messages';selectConversation('room:lobby');};
$('#daily-date').textContent=new Date().toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'}).replace('/','.');
async function checkHealth() {try{await api('/api/health');$('#server-status').textContent='小客厅已就绪 · 连接设置';$('#server-status-dot').classList.remove('offline');}catch{$('#server-status').textContent='服务端未连接 · 点击设置';$('#server-status-dot').classList.add('offline');}}
async function boot() {
  try {const config=await api('/desktop/config');state.server=config.server_url;state.native=config.native;await checkHealth();await bootstrap({initial:true});connectEvents();}
  catch(error){showAuth(error.status===401?'':error.message);}
}
setInterval(()=>{if(!state.me)checkHealth();},15000);
document.addEventListener('DOMContentLoaded',boot,{once:true});
