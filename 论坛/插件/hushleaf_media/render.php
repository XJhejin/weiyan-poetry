<?php
/* Original HushLeaf extension; Discuz! core remains unchanged. */
if (!defined('IN_DISCUZ')) { exit('Access Denied'); }

function hushleaf_media_escape($text) {
    return htmlspecialchars($text, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function hushleaf_media_size($value, $height = false) {
    if (!$height && preg_match('/^([1-9][0-9]?|100)%$/D', $value)) { return $value; }
    if (!preg_match('/^[1-9][0-9]{1,3}$/D', $value)) { return false; }
    $number = (int)$value;
    return $number >= ($height ? 40 : 120) && $number <= 1920 ? $number.'px' : false;
}

function hushleaf_media_url($value) {
    if (strlen($value) > 2048 || preg_match('/[\x00-\x20\x7f<>"\'\\\\]/', $value)) { return false; }
    $parts = parse_url($value);
    if (!$parts || strtolower($parts['scheme'] ?? '') !== 'https' || empty($parts['host']) ||
        isset($parts['user']) || isset($parts['pass']) || !filter_var($value, FILTER_VALIDATE_URL)) { return false; }
    $host = strtolower($parts['host']);
    // Reject literal loopback/private endpoints and ambiguous numeric host forms.
    if ($host === 'localhost' || str_ends_with($host, '.localhost') || str_ends_with($host, '.local') ||
        !str_contains($host, '.') || (preg_match('/^(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*$/iD', $host) &&
        !filter_var($host, FILTER_VALIDATE_IP)) || (filter_var(trim($host, '[]'), FILTER_VALIDATE_IP) &&
        !filter_var(trim($host, '[]'), FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE))) { return false; }
    return $value;
}

function hushleaf_media_provider($url) {
    $parts = parse_url($url);
    $host = strtolower($parts['host']);
    $path = $parts['path'] ?? '';
    parse_str($parts['query'] ?? '', $query);
    if (in_array($host, ['www.bilibili.com', 'bilibili.com'], true) &&
        preg_match('~^/video/(BV[A-Za-z0-9]{10})(?:/|$)~D', $path, $match)) {
        return 'https://player.bilibili.com/player.html?bvid='.$match[1].'&page=1&autoplay=0';
    }
    $video = $host === 'youtu.be' ? ltrim($path, '/') :
        (in_array($host, ['youtube.com', 'www.youtube.com', 'm.youtube.com'], true) ? ($query['v'] ?? '') : '');
    if (preg_match('/^[A-Za-z0-9_-]{11}$/D', $video)) {
        return 'https://www.youtube-nocookie.com/embed/'.$video.'?autoplay=0';
    }
    return false;
}

function hushleaf_media_render($type, $width, $height, $source, $attachmentResolver = null) {
    if (!in_array($type, ['video', 'audio', 'web'], true)) { return false; }
    $width = hushleaf_media_size($width);
    $height = hushleaf_media_size($height, true);
    if (!$width || !$height) { return false; }
    $attachment = false;
    if ($type !== 'web' && preg_match('/^attach:([1-9][0-9]{0,9})$/D', $source, $match)) {
        $url = $attachmentResolver ? $attachmentResolver((int)$match[1], $type) : false;
        $attachment = true;
    } else { $url = hushleaf_media_url($source); }
    if (!$url) { return false; }
    $embedded = $type === 'video' && !$attachment ? hushleaf_media_provider($url) : false;
    $href = hushleaf_media_escape($url);
    $style = 'width:'.$width.';max-width:100%;';
    $html = '<div class="hl-media hl-media-'.$type.'" style="'.$style.'">';
    if ($type === 'web' || $embedded) {
        $iframe = hushleaf_media_escape($embedded ?: $url);
        // Only fixed third-party video providers may keep their own origin.
        $sandbox = 'allow-scripts allow-presentation'.($embedded ? ' allow-same-origin' : '');
        $html .= '<iframe src="'.$iframe.'" title="'.($type === 'web' ? '嵌入网页' : '视频播放器').'" style="height:'.$height.';width:100%;border:0" loading="lazy" sandbox="'.$sandbox.'" allow="fullscreen; encrypted-media; picture-in-picture" allowfullscreen referrerpolicy="no-referrer"></iframe>';
    } else {
        $html .= '<'.$type.' controls preload="none" '.($type === 'video' ? 'playsinline ' : '').'style="width:100%;height:'.$height.'" src="'.$href.'">当前浏览器无法播放，请打开原文件。</'.$type.'>';
    }
    $html .= '<a class="hl-media-open" href="'.$href.'" target="_blank" rel="noopener noreferrer">'.($type === 'web' ? '打开原网页' : '打开原文件').'</a></div>';
    return $html;
}
