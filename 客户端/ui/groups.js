'use strict';
const roleName=role=>({owner:'群主',admin:'管理员',member:'成员'}[role]||'成员');
function renderGroupInvites(){
  let button=$('#group-invites');
  if(!button){button=document.createElement('button');button.id='group-invites';button.className='group-invite-badge';$('#new-chat').before(button);button.onclick=openGroupInvites;}
  button.hidden=!state.groupInvites?.length;
  button.textContent='邀请 '+(state.groupInvites?.length||0);
}
function openNewChat(){
  modal('发起聊天',`<div class="group-start-options"><button id="find-friends">${icons('chat')}<b>添加朋友 / 私聊</b><span>通过账号或昵称查找成员</span></button><button id="create-group">${icons('users')}<b>创建群聊</b><span>与多位朋友共同交流</span></button><button id="show-invitations">${icons('link')}<b>群聊邀请</b><span>${state.groupInvites?.length||0} 条待处理邀请</span></button></div>`);
  $('#find-friends').onclick=openFriends;$('#create-group').onclick=openCreateGroup;$('#show-invitations').onclick=openGroupInvites;
}
function openCreateGroup(){
  modal('创建群聊',`<form id="create-group-form"><label>群名称<input id="new-group-name" maxlength="40" placeholder="为这段共同的交流命名" required></label><div class="info-box">创建后可邀请朋友加入。群成员仅能接收加入后发送到其设备的消息。群名称、头像、公告和成员关系属于服务端可见的群资料。</div><p id="group-error" class="form-error"></p><div class="modal-actions"><button class="primary" type="submit">创建群聊</button></div></form>`);
  $('#create-group-form').onsubmit=async e=>{e.preventDefault();try{const data=await post('/api/groups',{action:'create',name:$('#new-group-name').value});await bootstrap();closeModal();await selectConversation(data.conversation_id);await openGroupInfo();}catch(error){$('#group-error').textContent=error.message;}};
}
function openGroupInvites(){
  modal('群聊邀请',`<div class="modal-results">${(state.groupInvites||[]).map(i=>`<div class="user-result">${avatar(i)}<div><b>${escape(i.name)}</b><p>${escape(i.inviter_name)} 邀请你加入</p></div><button data-invitation="${escape(i.id)}" data-answer="decline">拒绝</button><button class="primary" data-invitation="${escape(i.id)}" data-answer="accept">加入</button></div>`).join('')||emptyHTML('✉','暂无群聊邀请','收到的邀请会显示在这里。')}</div>`);
  $$('[data-invitation]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{const result=await post('/api/groups',{action:button.dataset.answer,invitation_id:button.dataset.invitation});await bootstrap();if(button.dataset.answer==='accept'){closeModal();state.view='messages';await selectConversation(result.conversation_id);}else openGroupInvites();}catch(e){toast(e.message);button.disabled=false;}});
}
async function groupAction(cid,action,extra={}){
  await post('/api/groups',{conversation_id:cid,action,...extra});
  await bootstrap({refreshMessages:true});
}
async function openGroupInfo(cid=state.active){
  try{
    const data=await api('/api/groups?conversation_id='+encodeURIComponent(cid));
    const c=data.conversation,g=c.group,admin=g.role!=='member';
    data.members.forEach(u=>state.users.set(u.id,u));
    modal('群聊资料',`<div class="profile-heading">${avatar(c)}<div><h3>${escape(c.name)}</h3><p>${g.member_count} 位成员 · 我是${roleName(g.role)}</p></div></div><p>${escape(c.description||'在此相识，交流文字与日常。')}</p>${g.announcement?`<div class="info-box"><b>群公告</b><p class="group-announcement">${escape(g.announcement)}</p></div>`:''}<div class="group-toolbar">${admin?'<button id="edit-group">编辑群资料</button>':''}${!g.invite_admin_only||admin?'<button id="invite-group">邀请成员</button>':''}<button id="group-nickname">我的群昵称</button></div>${g.mute_all?'<p class="group-muted-note">全员禁言已开启，群主与管理员可发言。</p>':''}<div class="group-members-heading"><h3>群成员</h3><input id="group-member-search" placeholder="搜索群成员" aria-label="搜索群成员"></div><div class="group-member-list">${data.members.map(u=>{
      const canManage=u.id!==state.me.id&&admin&&u.role!=='owner'&&(g.role==='owner'||u.role==='member');
      return `<div class="group-member" data-member-name="${escape((u.nickname+' '+u.username+' '+u.group_nickname).toLowerCase())}">${avatar(u,true,state.online.includes(u.id))}<div><b>${escape(u.group_nickname||u.nickname)}</b><small>@${escape(u.username)} · ${roleName(u.role)}${u.muted_until>Date.now()/1000?' · 禁言中':''}</small></div><button data-peer="${escape(u.id)}" ${u.id===state.me.id?'hidden':''}>私聊</button>${canManage?`<button data-manage-member="${escape(u.id)}">管理</button>`:''}</div>`;
    }).join('')}</div><div class="modal-actions"><button id="leave-group" class="group-danger">${g.role==='owner'?'解散群聊':'退出群聊'}</button><button class="primary" data-close>完成</button></div>`);
    $('#group-member-search').oninput=e=>$$('[data-member-name]').forEach(el=>el.hidden=!el.dataset.memberName.includes(e.target.value.toLowerCase()));
    if($('#edit-group'))$('#edit-group').onclick=()=>editGroup(c);
    if($('#invite-group'))$('#invite-group').onclick=()=>inviteGroup(c);
    $('#group-nickname').onclick=()=>{
      modal('我的群昵称',`<form id="nickname-form"><label>群内显示名称<input id="my-group-nickname" maxlength="24" value="${escape(g.nickname)}" placeholder="留空时使用个人昵称"></label><div class="modal-actions"><button class="primary" type="submit">保存</button></div></form>`);
      $('#nickname-form').onsubmit=async e=>{e.preventDefault();try{await groupAction(cid,'nickname',{nickname:$('#my-group-nickname').value});await openGroupInfo(cid);}catch(error){toast(error.message);}};
    };
    $$('[data-manage-member]').forEach(b=>b.onclick=()=>manageGroupMember(c,data.members.find(u=>u.id===b.dataset.manageMember)));
    $('#leave-group').onclick=()=>confirmGroupAction(c,g.role==='owner'?'dissolve':'leave',{},g.role==='owner'?'解散后成员将无法继续发言，未投递的群消息将停止投递。本机已保存的记录不会被远程删除。':'退出后不再收到此群的新消息，本机已保存的记录仍会保留。');
  }catch(e){toast(e.message);}
}
function confirmGroupAction(c,action,extra,message){
  modal('确认群操作',`<p>${escape(message)}</p><div class="modal-actions"><button id="group-action-back" class="text-button">取消</button><button id="group-action-confirm" class="primary">确认</button></div>`);
  $('#group-action-back').onclick=()=>openGroupInfo(c.id);
  $('#group-action-confirm').onclick=async()=>{try{await groupAction(c.id,action,extra);if(['dissolve','leave'].includes(action))closeModal();else await openGroupInfo(c.id);}catch(e){toast(e.message);}};
}
function manageGroupMember(c,u){
  modal('管理群成员',`<div class="profile-heading">${avatar(u)}<div><h3>${escape(u.group_nickname||u.nickname)}</h3><p>${roleName(u.role)}</p></div></div><div class="group-manage-options">${c.group.role==='owner'?`<button id="set-admin">${u.role==='admin'?'取消管理员':'设为管理员'}</button><button id="transfer-owner">转让群主</button>`:''}<label>禁言时长<select id="member-mute-time"><option value="0">解除禁言</option><option value="600">10 分钟</option><option value="3600">1 小时</option><option value="86400">1 天</option><option value="2592000">30 天</option></select></label><button id="mute-member">应用禁言设置</button><button id="kick-member" class="group-danger">移出群聊</button></div>`);
  if($('#set-admin'))$('#set-admin').onclick=()=>confirmGroupAction(c,'role',{user_id:u.id,role:u.role==='admin'?'member':'admin'},u.role==='admin'?'取消该成员的管理员权限？':'允许该成员管理群资料、禁言和移除普通成员？');
  if($('#transfer-owner'))$('#transfer-owner').onclick=()=>confirmGroupAction(c,'transfer',{user_id:u.id},'将群主转让给这位成员？转让后你将成为普通成员。');
  $('#mute-member').onclick=()=>confirmGroupAction(c,'mute',{user_id:u.id,seconds:Number($('#member-mute-time').value)},'应用所选的禁言设置？');
  $('#kick-member').onclick=()=>confirmGroupAction(c,'kick',{user_id:u.id},'移除这位成员？对方将不能继续收发此群消息，已保存在对方本机的记录无法撤回。');
}
function editGroup(c){
  let selectedAvatar=c.avatar,selectedColor=c.color;
  modal('编辑群资料',`<form id="edit-group-form"><label>群名称<input id="group-name" value="${escape(c.name)}" maxlength="40" required></label><label>群简介<textarea id="group-description" maxlength="200">${escape(c.description)}</textarea></label><label>群公告<textarea id="group-announcement" maxlength="1000">${escape(c.group.announcement)}</textarea></label><label>群头像</label><div class="avatar-options">${Object.keys(animals).map(a=>`<button class="avatar-option${selectedAvatar===a?' selected':''}" type="button" data-group-avatar="${a}">${avatar({avatar:a,color:c.color})}</button>`).join('')}</div><div id="group-avatar-editor"></div><div class="color-options">${['#ffe4ec','#eae5ff','#e2f1e8','#fff0d6','#dfefff'].map(color=>`<button type="button" class="color-option${color===c.color?' selected':''}" data-group-color="${color}" style="background:${color}" aria-label="${color}"></button>`).join('')}</div><label class="group-checkbox"><input type="checkbox" id="group-mute-all" ${c.group.mute_all?'checked':''}>全员禁言（群主和管理员除外）</label><label class="group-checkbox"><input type="checkbox" id="group-invite-admin" ${c.group.invite_admin_only?'checked':''}>仅允许群主和管理员邀请成员</label><p class="muted">群资料可由服务端读取，请勿在公告中放入私密聊天内容。</p><div class="modal-actions"><button type="submit" class="primary">保存群资料</button></div></form>`);
  const avatarEditor=createAvatarEditor($('#group-avatar-editor'),{value:selectedAvatar,color:selectedColor,onChange:next=>{selectedAvatar=next;$$('[data-group-avatar]').forEach(b=>b.classList.remove('selected'));}});
  $$('[data-group-avatar]').forEach(b=>b.onclick=()=>{selectedAvatar=b.dataset.groupAvatar;$$('[data-group-avatar]').forEach(x=>x.classList.toggle('selected',x===b));avatarEditor.setAvatar(selectedAvatar);});
  $$('[data-group-color]').forEach(b=>b.onclick=()=>{selectedColor=b.dataset.groupColor;$$('[data-group-color]').forEach(x=>x.classList.toggle('selected',x===b));avatarEditor.setColor(selectedColor);});
  $('#edit-group-form').onsubmit=async e=>{e.preventDefault();if(avatarEditor.hasPending()){toast('请先使用此头像，或取消裁剪。');return;}try{await groupAction(c.id,'settings',{name:$('#group-name').value,description:$('#group-description').value,announcement:$('#group-announcement').value,avatar:selectedAvatar,color:selectedColor,mute_all:$('#group-mute-all').checked,invite_admin_only:$('#group-invite-admin').checked});await openGroupInfo(c.id);}catch(error){toast(error.message);}};
}
function inviteGroup(c){
  modal('邀请成员',`<p>对方确认邀请后加入「${escape(c.name)}」。</p><div class="modal-search">${icons('search')}<input id="group-invite-search" placeholder="输入账号或昵称"></div><div id="group-invite-results" class="modal-results"></div>`);
  $('#group-invite-search').oninput=()=>{clearTimeout(searchTimer);const q=$('#group-invite-search').value.trim();searchTimer=setTimeout(async()=>{try{const data=await api('/api/users?q='+encodeURIComponent(q));if(!$('#group-invite-results'))return;$('#group-invite-results').innerHTML=data.users.map(u=>`<div class="user-result">${avatar(u)}<div><b>${escape(u.nickname)}</b><p>@${escape(u.username)}</p></div><button data-invite-user="${escape(u.id)}">邀请</button></div>`).join('');$$('[data-invite-user]').forEach(b=>b.onclick=async()=>{try{await groupAction(c.id,'invite',{user_id:b.dataset.inviteUser});b.textContent='已邀请';b.disabled=true;}catch(e){toast(e.message);}});}catch(e){toast(e.message);}},280);};
}
$('#new-chat').onclick=openNewChat;
const previousConversationInfo=$('#conversation-info').onclick;
$('#conversation-info').onclick=()=>currentConversation()?.kind==='group'?openGroupInfo():previousConversationInfo();
