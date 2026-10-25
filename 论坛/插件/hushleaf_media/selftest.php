<?php
/* Run with php selftest.php; this file is never installed as a web endpoint. */
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
define('IN_DISCUZ', true);
require __DIR__.'/hushleaf_media.class.php';
$checks = 0;
function check($value, $label) {
    global $checks;
    if (!$value) { throw new RuntimeException($label); }
    $checks++;
}
foreach (['https://example.com/a.mp4','https://example.com/a.mp4?a=1&b=2'] as $url) {
    $html = hushleaf_media_render('video', '100%', '405', $url);
    check(str_contains($html, '<video controls') && str_contains($html, 'preload="none"'), 'video');
    check(!str_contains($html, 'autoplay'), 'no_autoplay');
}
check(str_contains(hushleaf_media_render('audio','500','54','https://example.com/a.mp3'),'<audio controls'), 'audio');
$web = hushleaf_media_render('web','80%','640','https://example.com/');
check(str_contains($web,'sandbox="allow-scripts allow-presentation"'), 'sandbox');
check(!str_contains($web,'allow-same-origin'), 'isolated_origin');
check(str_contains($web,'打开原网页'), 'fallback');
foreach (['javascript:alert(1)','data:text/html,test','http://example.com/a', '//example.com/a',
    'https://example.com/"onload="evil','https://user:pass@example.com/','https://127.0.0.1/a',
    'https://192.168.1.1/a','https://127.1/a','https://0177.0.0.1/a','https://localhost/a',"https://example.com/\nscript"] as $url) {
    check(hushleaf_media_render('web','100%','480',$url) === false, 'reject_url');
}
foreach (['0','119','1921','101%','0%','100%;background:red','1e3','500px'] as $width) {
    check(hushleaf_media_render('web',$width,'480','https://example.com/') === false, 'reject_width');
}
foreach (['0','39','1921','50%','40;'] as $height) {
    check(hushleaf_media_render('web','100%',$height,'https://example.com/') === false, 'reject_height');
}
check(str_contains(hushleaf_media_render('video','100%','405','https://www.bilibili.com/video/BV1xx411c7mD/'),'player.bilibili.com'), 'bilibili');
check(str_contains(hushleaf_media_render('video','100%','405','https://youtu.be/abcdefghijk'),'youtube-nocookie.com'), 'youtube');
check(hushleaf_media_render('video','100%','405','attach:1') === false, 'no_attachment_bypass');
$html = hushleaf_media_render('audio','100%','54','attach:1',fn($aid,$type)=>'forum.php?mod=attachment&aid=test&noupdate=1');
check(str_contains($html,'&amp;aid=test'), 'escape_attributes');
check(!str_contains($html,'request='), 'native_permissions');
$plugin = new plugin_hushleaf_media();
foreach ([[false,1,0,1],[true,1,0,1],[false,0,0,1],[false,1,1,1],[false,1,0,0]] as $i=>$flags) {
    global $_G;
    $_G = ['discuzcodemessage'=>'[hlweb=100%,480]https://example.com/?a=1&amp;b=2[/hlweb]',
        'forum_discuzcode'=>['pcodecount'=>-1,'codehtml'=>[]]];
    $args=array_fill(0,13,0);[$args[2],$args[5],$args[9],$args[11]]=$flags;
    $plugin->discuzcode(['param'=>$args]);
    check(($i===0) === str_contains($_G['discuzcodemessage'], 'DISCUZ_CODE_'), 'permissions_and_signature');
}
echo json_encode(['ok'=>true,'checks'=>$checks]);
