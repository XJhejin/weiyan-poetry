package com.weiyan.poetry;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.database.Cursor;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.provider.Settings;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.TextView;
import android.widget.Toast;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Arrays;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private WebView web;
    private ValueCallback<Uri[]> chooser;
    private String baseUrl;
    private String downloadUrl;
    private String pendingApk;
    private static MainActivity active;
    private final java.util.concurrent.ExecutorService worker = Executors.newSingleThreadExecutor();

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        active = this;
        getWindow().setStatusBarColor(Color.rgb(255,248,250));
        getWindow().setNavigationBarColor(Color.rgb(255,248,250));
        TextView splash = new TextView(this);
        splash.setText("未言诗集\n\n正在准备本机加密空间…");
        splash.setTextColor(Color.rgb(158,105,129));splash.setTextSize(20);splash.setGravity(17);splash.setBackgroundColor(Color.rgb(255,248,250));
        setContentView(splash);
        worker.execute(() -> {
            try {
                File ui = new File(getNoBackupFilesDir(), "ui");
                copyAssets("ui", ui);
                if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
                String url = Python.getInstance().getModule("mobile").callAttr("start", new File(getNoBackupFilesDir(),"client").getPath(), ui.getPath()).toString();
                runOnUiThread(() -> showClient(url));
            } catch (Exception e) {
                runOnUiThread(() -> new AlertDialog.Builder(this).setTitle("启动未完成").setMessage(e.getMessage()).setPositiveButton("关闭",(d,w)->finish()).show());
            }
        });
    }

    private void copyAssets(String asset, File dest) throws IOException {
        String[] names = getAssets().list(asset);
        if (names != null && names.length > 0) {
            if (!dest.isDirectory() && !dest.mkdirs()) throw new IOException("无法准备本机界面");
            for (String name : names) copyAssets(asset + "/" + name, new File(dest, name));
        } else try (InputStream in = getAssets().open(asset); OutputStream out = new FileOutputStream(dest)) { copy(in,out); }
    }

    private void showClient(String url) {
        baseUrl = Uri.parse(url).buildUpon().path("").clearQuery().build().toString();
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(255,248,250));
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString() + " WeiyanAndroid/" + BuildConfig.VERSION_NAME);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (isLocal(request.getUrl().toString())) return false;
                if (request.isForMainFrame() && ("https".equals(request.getUrl().getScheme()) || "http".equals(request.getUrl().getScheme()))) {
                    new AlertDialog.Builder(MainActivity.this).setMessage("在浏览器中打开此链接？").setNegativeButton("取消",null)
                        .setPositiveButton("打开",(d,w)->startActivity(new Intent(Intent.ACTION_VIEW,request.getUrl()))).show();
                }
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (chooser != null) chooser.onReceiveValue(null);
                chooser = callback;
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
                String[] types = params.getAcceptTypes();
                if (types.length > 0 && !types[0].isEmpty()) intent.putExtra(Intent.EXTRA_MIME_TYPES,types);
                try { startActivityForResult(intent,101); } catch (Exception e) { chooser.onReceiveValue(null);chooser=null; }
                return true;
            }
        });
        web.setDownloadListener((url1, userAgent, disposition, mime, size) -> {
            if (!isLocal(url1) || !Uri.parse(url1).getPath().startsWith("/local-files/")) return;
            downloadUrl = url1;
            String name = "未言诗集文件";
            int index = disposition == null ? -1 : disposition.indexOf("filename*=UTF-8''");
            if (index >= 0) name = Uri.decode(disposition.substring(index + 17));
            Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/octet-stream").putExtra(Intent.EXTRA_TITLE,name);
            startActivityForResult(intent,102);
        });
        setContentView(web);
        if (Build.VERSION.SDK_INT >= 35) web.setOnApplyWindowInsetsListener((v,insets)->{android.graphics.Insets bars=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.ime());v.setPadding(bars.left,bars.top,bars.right,bars.bottom);return WindowInsets.CONSUMED;});
        web.loadUrl(url);
    }

    private boolean isLocal(String url) {
        Uri uri = Uri.parse(url), base = Uri.parse(baseUrl);
        return "http".equals(uri.getScheme()) && "127.0.0.1".equals(uri.getHost()) && uri.getPort() == base.getPort();
    }

    @Override protected void onActivityResult(int request,int result,Intent data) {
        super.onActivityResult(request,result,data);
        if (request == 101 && chooser != null) { chooser.onReceiveValue(result==RESULT_OK && data!=null?new Uri[]{data.getData()}:null);chooser=null; }
        if (request == 102 && result == RESULT_OK && data != null && downloadUrl != null) {
            Uri dest = data.getData();String source = downloadUrl;downloadUrl=null;
            String cookies = CookieManager.getInstance().getCookie(baseUrl);
            worker.execute(()->{
                HttpURLConnection connection=null;
                try {
                    connection=(HttpURLConnection)new URL(source).openConnection();
                    connection.setConnectTimeout(15000);connection.setReadTimeout(60000);connection.setRequestProperty("Cookie",cookies==null?"":cookies);
                    if(connection.getResponseCode()!=200)throw new IOException("文件暂时无法读取。");
                    try(InputStream in=connection.getInputStream();OutputStream out=getContentResolver().openOutputStream(dest)){copy(in,out);}
                    runOnUiThread(()->Toast.makeText(this,"文件已保存。保存位置由你选择，请自行保管。",Toast.LENGTH_LONG).show());
                }catch(Exception e){runOnUiThread(()->Toast.makeText(this,"保存未完成："+e.getMessage(),Toast.LENGTH_LONG).show());}
                finally{if(connection!=null)connection.disconnect();}
            });
        }
        if(request==103 && pendingApk!=null && (Build.VERSION.SDK_INT<26 || getPackageManager().canRequestPackageInstalls())) launchInstaller();
    }

    private static void copy(InputStream in,OutputStream out) throws IOException { byte[] bytes=new byte[65536];int n;while((n=in.read(bytes))!=-1)out.write(bytes,0,n); }

    public static void installUpdate(String path) throws Exception {
        MainActivity activity=active;
        if(activity==null)throw new IllegalStateException("请先打开客户端。");
        File file=new File(path).getCanonicalFile(),root=new File(activity.getNoBackupFilesDir(),"client/updates").getCanonicalFile();
        if(!file.getPath().startsWith(root.getPath()+File.separator)||!file.getName().endsWith(".apk"))throw new SecurityException("更新文件路径无效。");
        PackageManager pm=activity.getPackageManager();
        int flags=Build.VERSION.SDK_INT>=28?PackageManager.GET_SIGNING_CERTIFICATES:PackageManager.GET_SIGNATURES;
        PackageInfo fresh=pm.getPackageArchiveInfo(file.getPath(),flags),installed=pm.getPackageInfo(activity.getPackageName(),flags);
        if(fresh==null||!activity.getPackageName().equals(fresh.packageName)||fresh.versionCode<=installed.versionCode)throw new SecurityException("安装包与当前应用不匹配。");
        Signature[] a=Build.VERSION.SDK_INT>=28?fresh.signingInfo.getApkContentsSigners():fresh.signatures;
        Signature[] b=Build.VERSION.SDK_INT>=28?installed.signingInfo.getApkContentsSigners():installed.signatures;
        if(a.length!=1||b.length!=1||!Arrays.equals(a[0].toByteArray(),b[0].toByteArray()))throw new SecurityException("安卓应用签名不匹配，已阻止安装。");
        activity.pendingApk=file.getPath();
        activity.runOnUiThread(()->{
            if(Build.VERSION.SDK_INT>=26 && !pm.canRequestPackageInstalls()) {
                new AlertDialog.Builder(activity).setMessage("更新签名已验证。请在系统设置中允许本应用安装更新，然后返回继续。").setNegativeButton("取消",null)
                    .setPositiveButton("前往设置",(d,w)->activity.startActivityForResult(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+activity.getPackageName())),103)).show();
            }else activity.launchInstaller();
        });
    }

    private void launchInstaller() {
        if(pendingApk==null)return;
        Uri uri=new Uri.Builder().scheme("content").authority(getPackageName()+".updates").appendPath("apk").appendQueryParameter("path",pendingApk).build();
        startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(uri,"application/vnd.android.package-archive").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION));
        pendingApk=null;
    }
    @Override protected void onPause(){super.onPause();CookieManager.getInstance().flush();}
    @Override public void onBackPressed(){if(web==null){super.onBackPressed();return;}web.evaluateJavascript("window.weiyanBack ? window.weiyanBack() : false",answer->{if(!"true".equals(answer))moveTaskToBack(true);});}
    @Override protected void onDestroy(){if(active==this)active=null;if(web!=null){web.stopLoading();web.destroy();}worker.shutdown();super.onDestroy();}
}
