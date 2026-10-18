"""Group membership and moderation metadata. Message contents never enter here."""
import secrets
import time
from avatars import AVATARS, COLORS, normalize_avatar


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS groups(
      cid TEXT PRIMARY KEY REFERENCES conversations(id), announcement TEXT NOT NULL DEFAULT '',
      mute_all INTEGER NOT NULL DEFAULT 0, invite_admin_only INTEGER NOT NULL DEFAULT 0,
      closed INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS group_members(
      cid TEXT NOT NULL REFERENCES groups(cid), uid TEXT NOT NULL REFERENCES users(id),
      role TEXT NOT NULL DEFAULT 'member', nickname TEXT NOT NULL DEFAULT '',
      muted_until REAL NOT NULL DEFAULT 0, PRIMARY KEY(cid,uid));
    CREATE TABLE IF NOT EXISTS group_invites(
      id TEXT PRIMARY KEY, cid TEXT NOT NULL REFERENCES groups(cid),
      inviter TEXT NOT NULL REFERENCES users(id), invitee TEXT NOT NULL REFERENCES users(id),
      created REAL NOT NULL, UNIQUE(cid,invitee));
    ''')


def membership(db, cid, uid, error):
    row = db.execute('SELECT m.*,g.announcement,g.mute_all,g.invite_admin_only,g.closed '
                     'FROM group_members m JOIN groups g ON g.cid=m.cid WHERE m.cid=? AND m.uid=?', (cid, uid)).fetchone()
    if not row or row['closed']:
        raise error('你已不在这个群聊中。', 403)
    return row


def summary(db, cid, uid):
    row = db.execute('SELECT g.*,m.role,m.nickname,m.muted_until FROM groups g '
                     'JOIN group_members m ON m.cid=g.cid WHERE g.cid=? AND m.uid=?', (cid, uid)).fetchone()
    value = dict(row)
    value['member_count'] = db.execute('SELECT COUNT(*) FROM group_members WHERE cid=?', (cid,)).fetchone()[0]
    return value


def details(db, cid, uid, core):
    membership(db, cid, uid, core.ApiError)
    conv = core.conversation_json(db, core.access(db, cid, uid), uid)
    members = [dict(core.public_user(r), role=r['role'], group_nickname=r['group_nickname'], muted_until=r['muted_until'])
               for r in db.execute('SELECT u.*,m.role,m.nickname group_nickname,m.muted_until FROM group_members m '
                                   'JOIN users u ON u.id=m.uid WHERE m.cid=? ORDER BY CASE role WHEN \'owner\' THEN 0 WHEN \'admin\' THEN 1 ELSE 2 END,u.nickname', (cid,))]
    return {'conversation': conv, 'members': members}


def invitations(db, uid):
    return [dict(r) for r in db.execute('SELECT i.id,i.cid,i.inviter,i.created,c.name,c.avatar,c.color,u.nickname inviter_name '
                                       'FROM group_invites i JOIN groups g ON g.cid=i.cid JOIN conversations c ON c.id=i.cid '
                                       'JOIN users u ON u.id=i.inviter WHERE i.invitee=? AND g.closed=0 ORDER BY i.created DESC', (uid,))]


def check_speaking(db, cid, uid, error):
    if cid.startswith('room:') and cid != 'room:lobby':
        raise error('这个公共房间已关闭，请前往「-扉页-」。', 403)
    if not cid.startswith('group:'):
        return
    member = membership(db, cid, uid, error)
    if member['role'] == 'member' and (member['mute_all'] or member['muted_until'] > time.time()):
        raise error('你当前处于禁言状态。', 403)


def clear_deliveries(db, cid, uid=None):
    sql = 'DELETE FROM deliveries WHERE packet_id IN (SELECT id FROM receipts WHERE conversation_id=?)'
    if uid:
        sql += ' AND device_id IN (SELECT id FROM devices WHERE user_id=?)'
    db.execute(sql, (cid, uid) if uid else (cid,))


def action(db, uid, data, core):
    """Serialize membership changes against relay encryption-recipient validation."""
    error, text = core.ApiError, core.clean_text
    db.execute('BEGIN IMMEDIATE')
    op = data.get('action')
    affected = {uid}
    if op == 'create':
        name = text(data.get('name'), 40, '群名称')
        cid = 'group:' + secrets.token_hex(16)
        db.execute("INSERT INTO conversations VALUES (?,'group',?,'','flower','#ffe4ec',?)", (cid, name, core.now()))
        db.execute('INSERT INTO groups(cid) VALUES (?)', (cid,))
        db.execute('INSERT INTO group_members(cid,uid,role) VALUES (?,?,\'owner\')', (cid, uid))
        db.execute('INSERT INTO members(conversation_id,user_id) VALUES (?,?)', (cid, uid))
    elif op in ('accept', 'decline'):
        invitation = db.execute('SELECT i.* FROM group_invites i JOIN groups g ON g.cid=i.cid '
                                'WHERE i.id=? AND i.invitee=? AND g.closed=0', (data.get('invitation_id'), uid)).fetchone()
        if not invitation:
            raise error('邀请已失效。', 404)
        cid = invitation['cid']
        if op == 'accept':
            inviter = db.execute('SELECT role FROM group_members WHERE cid=? AND uid=?', (cid, invitation['inviter'])).fetchone()
            admin_only = db.execute('SELECT invite_admin_only FROM groups WHERE cid=?', (cid,)).fetchone()[0]
            if not inviter or (admin_only and inviter['role'] == 'member'):
                raise error('邀请人的权限已变更，请重新获取邀请。', 403)
            db.execute('INSERT OR IGNORE INTO group_members(cid,uid) VALUES (?,?)', (cid, uid))
            db.execute('INSERT OR IGNORE INTO members(conversation_id,user_id) VALUES (?,?)', (cid, uid))
        db.execute('DELETE FROM group_invites WHERE id=?', (invitation['id'],))
    else:
        cid = text(data.get('conversation_id'), 128, '群聊')
        member = membership(db, cid, uid, error)
        admin = member['role'] in ('owner', 'admin')
        if op == 'invite':
            if member['invite_admin_only'] and not admin:
                raise error('只有群主和管理员可以邀请成员。', 403)
            peer = text(data.get('user_id'), 64, '用户')
            if not db.execute("SELECT 1 FROM users WHERE id=? AND id!='system' AND disabled=0", (peer,)).fetchone():
                raise error('没有找到这位成员。', 404)
            if db.execute('SELECT 1 FROM group_members WHERE cid=? AND uid=?', (cid, peer)).fetchone():
                raise error('对方已经在群聊中。', 409)
            db.execute('INSERT INTO group_invites VALUES (?,?,?,?,?) ON CONFLICT(cid,invitee) DO UPDATE '
                       'SET id=excluded.id,inviter=excluded.inviter,created=excluded.created', (secrets.token_hex(16), cid, uid, peer, time.time()))
            affected.add(peer)
        elif op == 'settings':
            if not admin:
                raise error('只有群主和管理员可以修改群资料。', 403)
            conv = core.access(db, cid, uid)
            name = text(data.get('name', conv['name']), 40, '群名称')
            description = text(data.get('description', conv['description']), 200, '群简介', allow_empty=True)
            announcement = text(data.get('announcement', member['announcement']), 1000, '群公告', allow_empty=True)
            avatar, color = data.get('avatar', conv['avatar']), data.get('color', conv['color'])
            try:
                if avatar != conv['avatar']:
                    avatar = normalize_avatar(avatar)
            except ValueError as exc:
                raise error(str(exc)) from exc
            if color not in COLORS:
                raise error('请从列表中选择颜色。')
            for key in ('mute_all', 'invite_admin_only'):
                if key in data and not isinstance(data[key], bool):
                    raise error('群设置格式不正确。')
            db.execute('UPDATE conversations SET name=?,description=?,avatar=?,color=? WHERE id=?', (name, description, avatar, color, cid))
            db.execute('UPDATE groups SET announcement=?,mute_all=?,invite_admin_only=? WHERE cid=?',
                       (announcement, int(data.get('mute_all', member['mute_all'])), int(data.get('invite_admin_only', member['invite_admin_only'])), cid))
        elif op == 'nickname':
            nickname = text(data.get('nickname'), 24, '群昵称', allow_empty=True)
            db.execute('UPDATE group_members SET nickname=? WHERE cid=? AND uid=?', (nickname, cid, uid))
        elif op in ('kick', 'role', 'mute', 'transfer'):
            target = text(data.get('user_id'), 64, '成员')
            peer = membership(db, cid, target, error)
            affected.add(target)
            if target == uid:
                raise error('请选择其他群成员。')
            if op in ('role', 'transfer'):
                if member['role'] != 'owner':
                    raise error('只有群主可以设置管理员或转让群主。', 403)
                if op == 'transfer':
                    db.execute("UPDATE group_members SET role='member' WHERE cid=? AND uid=?", (cid, uid))
                    db.execute("UPDATE group_members SET role='owner',muted_until=0 WHERE cid=? AND uid=?", (cid, target))
                else:
                    role = data.get('role')
                    if role not in ('admin', 'member'):
                        raise error('群角色无效。')
                    db.execute('UPDATE group_members SET role=?,muted_until=0 WHERE cid=? AND uid=?', (role, cid, target))
            else:
                if not admin or peer['role'] == 'owner' or (member['role'] == 'admin' and peer['role'] == 'admin'):
                    raise error('你无权管理这位成员。', 403)
                if op == 'kick':
                    clear_deliveries(db, cid, target)
                    db.execute('DELETE FROM group_members WHERE cid=? AND uid=?', (cid, target))
                    db.execute('DELETE FROM members WHERE conversation_id=? AND user_id=?', (cid, target))
                    db.execute('DELETE FROM group_invites WHERE cid=? AND (invitee=? OR inviter=?)', (cid, target, target))
                else:
                    seconds = data.get('seconds', 0)
                    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 0 <= seconds <= 30 * 86400:
                        raise error('禁言时长应为 0 至 30 天。')
                    db.execute('UPDATE group_members SET muted_until=? WHERE cid=? AND uid=?', (time.time() + seconds if seconds else 0, cid, target))
        elif op == 'leave':
            if member['role'] == 'owner':
                raise error('请先转让群主，或解散群聊。')
            clear_deliveries(db, cid, uid)
            db.execute('DELETE FROM group_members WHERE cid=? AND uid=?', (cid, uid))
            db.execute('DELETE FROM members WHERE conversation_id=? AND user_id=?', (cid, uid))
            db.execute('DELETE FROM group_invites WHERE cid=? AND inviter=?', (cid, uid))
        elif op == 'dissolve':
            if member['role'] != 'owner':
                raise error('只有群主可以解散群聊。', 403)
            affected.update(r[0] for r in db.execute('SELECT uid FROM group_members WHERE cid=?', (cid,)))
            clear_deliveries(db, cid)
            db.execute('UPDATE groups SET closed=1 WHERE cid=?', (cid,))
            db.execute('DELETE FROM group_invites WHERE cid=?', (cid,))
            db.execute('DELETE FROM members WHERE conversation_id=?', (cid,))
        else:
            raise error('群操作不存在。')
    affected.update(r[0] for r in db.execute('SELECT uid FROM group_members WHERE cid=?', (cid,)))
    core.relay.cleanup(db)
    db.commit()
    core.EVENTS.publish({'type': 'conversations'}, affected)
    return {'ok': True, 'conversation_id': cid}
