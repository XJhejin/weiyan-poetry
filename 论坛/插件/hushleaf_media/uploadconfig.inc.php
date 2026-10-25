<?php
if (!defined('IN_DISCUZ')) { exit('Access Denied'); }
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
if (!$_G['uid'] || !hash_equals(FORMHASH, (string)($_GET['formhash'] ?? ''))) {
    http_response_code(403); echo json_encode(['error'=>'请先登录后重试。']); exit;
}
if (empty($_G['group']['allowpostattach'])) {
    http_response_code(403); echo json_encode(['error'=>'当前用户组没有上传附件权限。']); exit;
}
echo json_encode(['uid'=>(int)$_G['uid'],
    'hash'=>md5(substr(md5($_G['config']['security']['authkey']), 8).$_G['uid']),
    'maximum'=>min(1073741824, (int)$_G['group']['maxattachsize'] ?: 1073741824)]);
exit;
