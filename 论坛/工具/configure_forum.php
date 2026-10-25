<?php
/* Provision through the official installer and native Discuz! settings APIs. */
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);
$action = $argv[1] ?? '';
$root = '/opt/weiyan/forum/current/';
$secrets = json_decode(file_get_contents('/etc/weiyan-forum/secrets.json'), true, 512, JSON_THROW_ON_ERROR);
if ($action === 'database') {
    $socket = '/var/lib/weiyan-forum-db/run/mysql.sock';
    try {
        $db = new mysqli('localhost', 'root', $secrets['db_root_password'], '', 0, $socket);
    } catch (mysqli_sql_exception $error) {
        $db = new mysqli('localhost', 'root', '', '', 0, $socket);
        $rootPassword = $db->real_escape_string($secrets['db_root_password']);
        $db->query("ALTER USER 'root'@'localhost' IDENTIFIED BY '$rootPassword'");
    }
    $db->set_charset('utf8mb4');
    $db->query('CREATE DATABASE IF NOT EXISTS hushleaf_bbs CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci');
    $password = $db->real_escape_string($secrets['db_password']);
    $db->query("CREATE USER IF NOT EXISTS 'hushleaf_bbs'@'localhost' IDENTIFIED BY '$password'");
    $db->query("ALTER USER 'hushleaf_bbs'@'localhost' IDENTIFIED BY '$password'");
    $db->query("GRANT ALL PRIVILEGES ON hushleaf_bbs.* TO 'hushleaf_bbs'@'localhost'");
    echo json_encode(['ok'=>true, 'database_created'=>true]); exit;
}
chdir($root);
ini_set('mysqli.default_socket', '/var/lib/weiyan-forum-db/run/mysql.sock');
$_SERVER = array_merge($_SERVER, [
    'HTTP_HOST'=>'bbs.hushleaf.top', 'SERVER_NAME'=>'bbs.hushleaf.top', 'SERVER_ADDR'=>'127.0.0.1',
    'REMOTE_ADDR'=>'127.0.0.1', 'HTTPS'=>'on', 'HTTP_X_FORWARDED_PROTO'=>'https', 'SERVER_PORT'=>443,
    'REQUEST_METHOD'=>'GET', 'REQUEST_URI'=>'/forum.php', 'PHP_SELF'=>'/forum.php',
    'SCRIPT_NAME'=>'/forum.php', 'HTTP_USER_AGENT'=>'HushLeaf-Forum-Setup',
    'SCRIPT_FILENAME'=>$root.'forum.php', 'DOCUMENT_ROOT'=>rtrim($root, '/'),
    'QUERY_STRING'=>'', 'REMOTE_PORT'=>0,
]);
if ($action === 'install-config' || $action === 'install-schema') {
    if (file_exists($root.'data/install.lock')) { throw new RuntimeException('Installer is locked.'); }
    $_SERVER['PHP_SELF'] = $_SERVER['SCRIPT_NAME'] = '/install/index.php';
    $_SERVER['REQUEST_URI'] = '/install/index.php';
    $_SERVER['REQUEST_METHOD'] = 'POST';
    $_COOKIE['LANG'] = 'SC_UTF8';
    $_GET = ['view_off'=>'1', 'step'=>'3', 'install_ucenter'=>'standalone',
             'method'=>$action === 'install-config' ? 'db_init' : 'do_db_init'];
    $all = ['dbhost'=>'localhost', 'dbuser'=>'hushleaf_bbs', 'dbpw'=>$secrets['db_password'],
            'dbname'=>'hushleaf_bbs', 'tablepre'=>'hl_', 'username'=>'掇花者',
            'password'=>$secrets['admin_password'], 'email'=>'admin@hushleaf.top',
            'dzucfull'=>true, 'dzucstl'=>1, 'uid'=>1, 'myisam2innodb'=>true];
    $_POST = ['dbinfo'=>['dbhost'=>$all['dbhost'], 'dbname'=>$all['dbname'], 'dbuser'=>$all['dbuser'],
                        'dbpw'=>$all['dbpw'], 'tablepre'=>'hl_', 'adminemail'=>$all['email']],
              'admininfo'=>['username'=>$all['username'], 'password'=>$all['password'],
                           'password2'=>$all['password'], 'email'=>$all['email']],
              'allinfo'=>base64_encode(serialize($all))];
    $_REQUEST = array_merge($_GET, $_POST);
    require $root.'install/index.php'; exit;
}
if ($action === 'harden-config') {
    require $root.'config/config_global.php';
    $_config['output']['forcehttps'] = 1;
    $_config['output']['upgradeinsecure'] = 1;
    $_config['output']['tplrefresh'] = 1;
    $_config['output']['language'] = 'zh-cn';
    $_config['cookie']['cookiepre'] = 'HLForum_';
    $_config['cookie']['cookiedomain'] = '';
    $_config['lang'] = 'SC_UTF8';
    $_config['admincp']['founder'] = '1';
    $_config['security']['urlxssdefend'] = true;
    file_put_contents($root.'config/config_global.php', "<?php\n// Generated locally; never distribute this file.\n\$_config = ".var_export($_config, true).";\n");
    echo json_encode(['ok'=>true]); exit;
}
// Core initialization intentionally clears caller globals. Keep maintenance state
// in a local scope, while allowing the native installer above its global scope.
(function ($action, $root, $secrets) {
global $_G;
define('IN_ADMINCP', true);
define('CURSCRIPT', 'forum');
require $root.'source/class/class_core.php';
require_once $root.'source/discuz_version.php';
require_once $root.'source/mitframe_version.php';
set_exception_handler(function (Throwable $error) {
    fwrite(STDERR, get_class($error).': '.$error->getMessage().' at '.basename($error->getFile()).':'.$error->getLine()."\n");
    exit(1);
});
$discuz = C::app();
$discuz->init_cron = false;
$discuz->init_session = false;
$discuz->init_user = false;
$discuz->init_mobile = false;
$discuz->init_misc = false;
$discuz->init();
require_once libfile('function/cache');
if ($action === 'theme' || $action === 'configure') {
    require_once libfile('function/admincp');
    require_once libfile('function/importdata');
    $existing = DB::result_first("SELECT styleid FROM ".DB::table('common_style')." WHERE name='未言诗集 · HushLeaf'");
    import_styles(1, $existing ? './template/hushleaf' : 'hushleaf', (int)$existing, 1, 0, 0, 1);
    $hushleafStyle = DB::result_first("SELECT styleid FROM ".DB::table('common_style')." WHERE name='未言诗集 · HushLeaf'");
    table_common_setting::t()->update_batch(['styleid2'=>(int)$hushleafStyle,'styleid3'=>(int)$hushleafStyle]);
    if (empty($_G['setting']['hushleaf_initial_preferences'])) {
        $mobile = $_G['setting']['mobile'];
        $mobile['allowmobile'] = 1;
        $mobile['mobileforward'] = 1;
        $mobile['mobileregister'] = 1;
        $mobile['forum']['index'] = 0;
        table_common_setting::t()->update_batch(['mobile'=>$mobile,'hushleaf_initial_preferences'=>1]);
        DB::update('forum_forumfield', ['icon'=>'https://bbs.hushleaf.top/static/hushleaf/logo.svg'],
                   ['icon'=>'static/hushleaf/logo.svg']);
    }
    if ($action === 'configure') {
        $mobile = $_G['setting']['mobile'];
        $mobile['allowmobile'] = 1;
        $mobile['mobileforward'] = 1;
        $mobile['mobileregister'] = 1;
        $mobile['forum']['index'] = 0;
        $settings = [
            'bbname'=>'未言诗集', 'sitename'=>'未言诗集', 'siteurl'=>'https://bbs.hushleaf.top/',
            'adminemail'=>'admin@hushleaf.top', 'timeoffset'=>'8', 'regstatus'=>'1',
            'bbclosed'=>'0', 'regverify'=>'0', 'mobile'=>$mobile, 'forumstatus'=>'1',
            'portalstatus'=>'1', 'groupstatus'=>'1', 'homestatus'=>'1', 'pmstatus'=>'1',
            'forumallowside'=>'0', 'defaultindex'=>'forum.php',
            'seotitle'=>['forum'=>'未言诗集 · HushLeaf 社区'],
            'seodescription'=>['forum'=>'未言诗集社区，供掇花者分享诗文、记录日常、交流想法。'],
            'seokeywords'=>['forum'=>'未言诗集,HushLeaf,诗文,社区,掇花者'],
        ];
        table_common_setting::t()->update_batch($settings);
        DB::update('forum_forum', ['name'=>'诗集目录', 'forumcolumns'=>0], ['fid'=>1]);
        $baseForum = DB::fetch_first('SELECT * FROM '.DB::table('forum_forum').' WHERE fid=2');
        $baseField = DB::fetch_first('SELECT * FROM '.DB::table('forum_forumfield').' WHERE fid=2');
        $boards = [2=>['-扉页-','初次相逢，从自我介绍和一声问候开始。'],
                   3=>['诗与远方','原创诗文、阅读随笔，以及值得被记下的句子。'],
                   4=>['日常拾光','分享生活中的所见、所感，收存平凡时刻。'],
                   5=>['意见与回声','产品交流、使用反馈与社区建议。']];
        foreach ($boards as $fid=>$board) {
            $forum = $baseForum;
            $forum['fid']=$fid; $forum['fup']=1; $forum['name']=$board[0];
            $forum['displayorder']=$fid; $forum['styleid']=0;
            $forum['allowpostspecial']=31;
            DB::insert('forum_forum', $forum, false, true);
            $field=$baseField; $field['fid']=$fid; $field['description']=$board[1];
            $field['icon']='https://bbs.hushleaf.top/static/hushleaf/logo.svg';
            DB::insert('forum_forumfield', $field, false, true);
        }
        // Keep the original navigation actions and add the project website.
        if (!DB::result_first('SELECT id FROM '.DB::table('common_nav')." WHERE url='https://www.hushleaf.top/'")) {
            DB::insert('common_nav', ['name'=>'官网与下载','url'=>'https://www.hushleaf.top/',
                'identifier'=>'hushleaf_official','type'=>0,'available'=>1,'displayorder'=>20,
                'target'=>1,'level'=>0,'subtype'=>0,'subcols'=>0,'highlight'=>0,'navtype'=>0]);
        }
        DB::update('common_usergroup', ['grouptitle'=>'掇花者'], ['groupid'=>10]);
        file_put_contents($root.'data/install.lock', gmdate('c'));
    }
    updatecache();
    echo json_encode(['ok'=>true,'theme'=>'hushleaf','forums'=>4]); exit;
}
if ($action === 'verify') {
    $forums=DB::fetch_all('SELECT fid,name FROM '.DB::table('forum_forum')." WHERE type='forum' ORDER BY fid");
    $admin=DB::fetch_first('SELECT uid,username,adminid FROM '.DB::table('common_member').' WHERE uid=1');
    $uc=DB::fetch_first('SELECT password FROM '.DB::table('ucenter_members').' WHERE uid=1');
    $verified=isset($secrets['admin_password']) ? password_verify($secrets['admin_password'],$uc['password']) : true;
    echo json_encode(['ok'=>(count($forums)===4 && (int)$admin['adminid']===1 && $verified),
        'forums'=>$forums,'admin_username'=>$admin['username'],'admin_password_verified'=>$verified,
        'version'=>DISCUZ_VERSION.DISCUZ_SUBVERSION,'install_locked'=>file_exists($root.'data/install.lock')],JSON_UNESCAPED_UNICODE); exit;
}
throw new RuntimeException('Unknown maintenance action.');
})($action, $root, $secrets);
