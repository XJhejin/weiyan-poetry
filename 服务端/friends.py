"""Mutual friendships require explicit acceptance; direct messaging stays open."""
import secrets
import time


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS friend_requests(
      id TEXT PRIMARY KEY, sender TEXT NOT NULL REFERENCES users(id),
      recipient TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
      UNIQUE(sender,recipient));
    ''')
    if not db.execute("SELECT 1 FROM settings WHERE key='mutual_friends_v1'").fetchone():
        # Earlier clients added a one-way contact merely by opening a DM.
        db.execute('DELETE FROM contacts WHERE NOT EXISTS(SELECT 1 FROM contacts other '
                   'WHERE other.user_id=contacts.peer_id AND other.peer_id=contacts.user_id)')
        db.execute("INSERT INTO settings VALUES ('mutual_friends_v1','1')")


def pending(db, uid):
    return [dict(r) for r in db.execute('SELECT f.id,f.sender,f.recipient,f.created,u.username,u.nickname,u.avatar,u.color '
                                       'FROM friend_requests f JOIN users u ON u.id=f.sender WHERE f.recipient=? ORDER BY f.created DESC', (uid,))]


def outgoing(db, uid):
    return [r[0] for r in db.execute('SELECT recipient FROM friend_requests WHERE sender=?', (uid,))]


def action(db, uid, data, core):
    db.execute('BEGIN IMMEDIATE')
    op = data.get('action', 'request')
    affected = {uid}
    if op == 'request':
        peer = core.clean_text(data.get('user_id'), 64, '用户')
        if peer == uid or not db.execute("SELECT 1 FROM users WHERE id=? AND id!='system' AND disabled=0", (peer,)).fetchone():
            raise core.ApiError('没有找到这位朋友。', 404)
        if db.execute('SELECT 1 FROM contacts WHERE user_id=? AND peer_id=?', (uid, peer)).fetchone():
            return {'ok': True, 'already_friends': True}
        db.execute('INSERT OR IGNORE INTO friend_requests VALUES (?,?,?,?)', (secrets.token_hex(16), uid, peer, time.time()))
        affected.add(peer)
    elif op in ('accept', 'decline'):
        row = db.execute('SELECT * FROM friend_requests WHERE id=? AND recipient=?', (data.get('request_id'), uid)).fetchone()
        if not row:
            raise core.ApiError('好友申请已失效。', 404)
        peer = row['sender']
        if op == 'accept':
            for first, second in ((uid, peer), (peer, uid)):
                db.execute('INSERT OR IGNORE INTO contacts VALUES (?,?)', (first, second))
            db.execute('DELETE FROM friend_requests WHERE sender=? AND recipient=?', (uid, peer))
        db.execute('DELETE FROM friend_requests WHERE id=?', (row['id'],))
        affected.add(peer)
    elif op == 'remove':
        peer = core.clean_text(data.get('user_id'), 64, '用户')
        db.execute('DELETE FROM contacts WHERE (user_id=? AND peer_id=?) OR (user_id=? AND peer_id=?)', (uid, peer, peer, uid))
        affected.add(peer)
    else:
        raise core.ApiError('好友操作不存在。')
    db.commit()
    core.EVENTS.publish({'type': 'conversations'}, affected)
    return {'ok': True}
