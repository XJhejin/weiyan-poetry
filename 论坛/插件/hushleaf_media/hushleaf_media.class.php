<?php
if (!defined('IN_DISCUZ')) { exit('Access Denied'); }
require_once __DIR__.'/render.php';

class plugin_hushleaf_media {
    public function discuzcode($context) {
        global $_G;
        $args = $context['param'];
        // Never render executable media in signatures, code examples or BBCode-off posts.
        if (!empty($args[2]) || empty($args[5]) || !empty($args[9]) || empty($args[11])) { return; }
        $pid = (int)($args[12] ?? 0);
        $resolver = function ($aid, $type) use ($pid) {
            if (!$pid) { return false; }
            $index = table_forum_attachment::t()->fetch($aid);
            if (!$index || (int)$index['pid'] !== $pid || empty($index['tid'])) { return false; }
            $attach = table_forum_attachment_n::t()->fetch_attachment('aid:'.$aid, $aid);
            $extensions = $type === 'video' ? ['mp4','webm','ogv','mov','m4v'] : ['mp3','m4a','ogg','wav','aac','flac','opus'];
            if (!$attach || !in_array(strtolower(pathinfo($attach['filename'], PATHINFO_EXTENSION)), $extensions, true)) { return false; }
            return 'plugin.php?id=hushleaf_media:stream&aid='.$aid;
        };
        $_G['discuzcodemessage'] = preg_replace_callback(
            '~\[hl(video|audio|web)=([0-9%]+),([0-9]+)\]([^\[\]\r\n]{1,4096})\[/hl\1\]~i',
            function ($match) use ($resolver) {
                global $_G;
                $source = html_entity_decode($match[4], ENT_QUOTES | ENT_HTML5, 'UTF-8');
                $html = hushleaf_media_render(strtolower($match[1]), $match[2], $match[3], $source, $resolver);
                if ($html === false) { return $match[0]; }
                $slot = ++$_G['forum_discuzcode']['pcodecount'];
                $_G['forum_discuzcode']['codehtml'][$slot] = $html;
                return "[\tDISCUZ_CODE_$slot\t]";
            }, $_G['discuzcodemessage']);
    }

    public function global_footer() {
        return '<link rel="stylesheet" href="static/hushleaf/media.css?v=3"><script defer src="static/hushleaf/media.js?v=3"></script>';
    }

    public function global_footer_mobile() { return $this->global_footer(); }
}

class mobileplugin_hushleaf_media extends plugin_hushleaf_media {}
