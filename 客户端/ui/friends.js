'use strict';
function isFriend(uid){return state.contacts.some(u=>u.id===uid);}
function friendButton(uid){return isFriend(uid)?'<span class="friend-status">已是好友</span>':`<button data-add-friend="${escape(uid)}" ${state.outgoingFriendRequests?.includes(uid)?'disabled':''}>${state.outgoingFriendRequests?.includes(uid)?'申请已发送':'添加好友'}</button>`;}
async function requestFriend(uid){
  try{await post('/api/friends',{action:'request',user_id:uid});await bootstrap();$$('[data-add-friend]').filter(b=>b.dataset.addFriend===uid).forEach(b=>{b.textContent=isFriend(uid)?'已是好友':'申请已发送';b.disabled=true;});toast(isFriend(uid)?'你们已经是好友。':'好友申请已发送，等待对方同意。');}catch(e){toast(e.message);}
}
function renderFriendRequests(){
  let button=$('#friend-requests');
  if(!button){button=document.createElement('button');button.id='friend-requests';button.className='friend-request-badge';$('#new-chat').before(button);button.onclick=openFriendRequests;}
  button.hidden=!state.friendRequests?.length;button.textContent='好友 '+(state.friendRequests?.length||0);
}
function updateStrangerBanner(){
  let banner=$('#stranger-banner');
  if(!banner){banner=document.createElement('div');banner.id='stranger-banner';banner.className='stranger-banner';$('.chat-header').after(banner);}
  const c=currentConversation();banner.hidden=c?.kind!=='direct'||isFriend(c.peer.id);
  if(!banner.hidden)banner.innerHTML=`${icons('users')}<span>你们还不是好友，请注意保护个人信息。</span>${friendButton(c.peer.id)}`;
}
function openFriendRequests(){
  modal('好友申请',`<div class="modal-results">${(state.friendRequests||[]).map(r=>`<div class="user-result">${avatar(r)}<div><b>${escape(r.nickname)}</b><p>@${escape(r.username)} 希望添加你为好友</p></div><button data-friend-answer="decline" data-request-id="${escape(r.id)}">拒绝</button><button class="primary" data-friend-answer="accept" data-request-id="${escape(r.id)}">同意</button></div>`).join('')||emptyHTML('✉','暂无好友申请','收到的好友申请会显示在这里。')}</div>`);
  $$('[data-friend-answer]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await post('/api/friends',{action:b.dataset.friendAnswer,request_id:b.dataset.requestId});await bootstrap();openFriendRequests();}catch(e){toast(e.message);b.disabled=false;}});
}
// Preserve direct messaging as a separate action from mutual friendship.
openFriends=function(){
  modal('和新朋友打个招呼',`<p>使用 @用户名 精确查找账号，或直接搜索昵称。</p><div class="modal-search">${icons('search')}<input id="friend-search" placeholder="@用户名 或昵称" autocomplete="off"></div><div id="friend-results" class="modal-results">${emptyHTML('⌕','查找朋友','对方同意申请后建立好友关系，也可以直接私聊。')}</div>`);
  $('#friend-search').focus();
  $('#friend-search').oninput=()=>{clearTimeout(searchTimer);const q=$('#friend-search').value.trim();searchTimer=setTimeout(async()=>{if(!q){$('#friend-results').innerHTML='';return;}try{const data=await api('/api/users?q='+encodeURIComponent(q));if(!$('#friend-results'))return;data.users.forEach(u=>state.users.set(u.id,u));$('#friend-results').innerHTML=data.users.map(u=>`<div class="user-result">${avatar(u)}<div><b>${escape(u.nickname)}</b><p>@${escape(u.username)}</p></div><button data-peer="${escape(u.id)}">私聊</button>${friendButton(u.id)}</div>`).join('')||emptyHTML('☁','暂时没有找到这位朋友','请确认用户名或尝试其他昵称。');}catch(e){toast(e.message);}},280);};
};
const existingOpenNewChat=openNewChat;
openNewChat=function(){existingOpenNewChat();$('.group-start-options').insertAdjacentHTML('beforeend',`<button id="show-friend-requests">${icons('users')}<b>好友申请</b><span>${state.friendRequests?.length||0} 条待处理申请</span></button>`);$('#find-friends').onclick=openFriends;$('#show-friend-requests').onclick=openFriendRequests;};
$('#new-chat').onclick=()=>openNewChat();
const existingOpenProfile=openProfile;
openProfile=function(user=state.me){existingOpenProfile(user);if(user.id!==state.me.id&&user.id!=='system')$('.modal-actions').insertAdjacentHTML('afterbegin',friendButton(user.id));};
document.addEventListener('click',e=>{const b=e.target.closest('[data-add-friend]');if(b)requestFriend(b.dataset.addFriend);});
