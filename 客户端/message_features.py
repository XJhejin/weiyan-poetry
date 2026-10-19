"""Structured quotes and explicit account mentions, inside the encrypted body."""
import re

IDENTITY = re.compile(r'[A-Za-z0-9_-]{1,80}')


def checked_mentions(body, mentions):
    if mentions is None:
        return []
    if not isinstance(mentions, list) or len(mentions) > 50:
        raise ValueError('@ 成员列表无效。')
    result, end = [], 0
    for item in mentions:
        if not isinstance(item, dict):
            raise ValueError('@ 成员格式无效。')
        uid, start, length = item.get('user_id'), item.get('start'), item.get('length')
        if (not isinstance(uid, str) or not IDENTITY.fullmatch(uid)
                or type(start) is not int or type(length) is not int
                or start < end or not 2 <= length <= 80 or start + length > len(body)
                or body[start] != '@' or any(c in '\r\n' for c in body[start:start + length])):
            raise ValueError('@ 成员位置无效，请重新选择成员。')
        result.append({'user_id': uid, 'start': start, 'length': length})
        end = start + length
    return result


def checked_quote(quote):
    if quote is None:
        return None
    if not isinstance(quote, dict):
        raise ValueError('引用消息格式无效。')
    if (type(quote.get('id')) is not int or not 0 < quote['id'] <= 9223372036854775807
            or any(not isinstance(quote.get(k), str) or not IDENTITY.fullmatch(quote[k]) for k in ('client_id', 'sender_id'))
            or not isinstance(quote.get('sender_name'), str) or len(quote['sender_name']) > 80
            or not isinstance(quote.get('excerpt'), str) or len(quote['excerpt']) > 160
            or quote.get('kind') not in ('text', 'image', 'video', 'file')):
        raise ValueError('引用消息信息无效。')
    return {k: quote[k] for k in ('id', 'client_id', 'sender_id', 'sender_name', 'excerpt', 'kind')} | ({'recalled': True} if quote.get('recalled') is True else {})


def quote_from(message):
    attachment = message.get('attachment') or {}
    kind = attachment.get('kind') or ('image' if message.get('image') else 'text')
    label = {'image': '[图片]', 'video': '[视频]', 'file': '[文件]'}.get(kind, '')
    excerpt = message.get('body') or (label + ' ' + attachment.get('name', '')).strip()
    return {'id': message['id'], 'client_id': message['client_id'], 'sender_id': message['sender']['id'],
            'sender_name': message['sender']['nickname'], 'excerpt': '消息已撤回' if message.get('recalled') else excerpt[:160],
            'kind': kind, **({'recalled': True} if message.get('recalled') else {})}


def mentioned(message, uid):
    # Never trust an unencrypted or caller-supplied 'mentioned' flag.
    return any(item['user_id'] == uid for item in checked_mentions(message.get('body', ''), message.get('mentions')))


def scrub_quote(value, target, sender_id):
    """Drop cached quote excerpts when the corresponding durable recall arrives."""
    if isinstance(value, dict):
        quote = value.get('quote')
        if isinstance(quote, dict) and quote.get('id') == target['id'] and quote.get('client_id') == target['client_id'] and quote.get('sender_id') == sender_id:
            value['quote'] = {**quote, 'excerpt': '消息已撤回', 'recalled': True}
        for child in value.values():
            scrub_quote(child, target, sender_id)
    elif isinstance(value, list):
        for child in value:
            scrub_quote(child, target, sender_id)
