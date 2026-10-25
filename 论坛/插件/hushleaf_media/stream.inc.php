<?php
if (!defined('IN_DISCUZ')) { exit('Access Denied'); }
header('Cache-Control: private, no-store');
header('Referrer-Policy: no-referrer');
$aid = (int)($_GET['aid'] ?? 0);
$index = $aid ? table_forum_attachment::t()->fetch($aid) : false;
if (!$index || empty($index['tid']) || empty($index['pid'])) { http_response_code(404); exit; }
$attach = table_forum_attachment_n::t()->fetch_attachment('aid:'.$aid, $aid);
$thread = table_forum_thread::t()->fetch_thread($index['tid']);
$post = $thread ? table_forum_post::t()->fetch_post($thread['posttableid'], $index['pid'], false) : false;
if (!$attach || !$thread || !$post || $thread['displayorder'] < 0 || $post['invisible'] != 0 ||
    !in_array(strtolower(pathinfo($attach['filename'], PATHINFO_EXTENSION)),
        ['mp4','webm','ogv','mov','m4v','mp3','m4a','ogg','wav','aac','flac','opus'], true)) {
    http_response_code(404); exit;
}
if ($_G['adminid'] < 1 && (int)$thread['readperm'] > (int)$_G['group']['readaccess'] && $_G['uid'] != $thread['authorid']) {
    http_response_code(403); exit;
}
// Generate the native permission-checked URL for this request's actual session.
// Player markup contains only the stable gateway URL, never another viewer's token.
dheader('location: forum.php?mod=attachment&aid='.aidencode($aid, 0, $index['tid']).'&noupdate=1&mobile=no');
exit;
