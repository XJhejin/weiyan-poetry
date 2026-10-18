package com.weiyan.poetry;

import android.Manifest;
import android.app.*;
import android.content.Intent;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.IBinder;

/** Visible foreground service keeps the encrypted receiver active without the WebView. */
public class NotificationService extends Service {
    private static volatile NotificationService active;
    private static Context application;
    private int unread;
    private NotificationManager manager;
    private static final String RUNNING="encrypted_receiver",LEGACY_MESSAGES="encrypted_messages",MESSAGES="encrypted_messages_popup_v2";
    static void initialize(Context context){application=context.getApplicationContext();preferences().edit().putBoolean("initialized",true).apply();}
    private static SharedPreferences preferences(){return application.getSharedPreferences("background-reception",Context.MODE_PRIVATE);}
    public static boolean isEnabled(){return application!=null&&preferences().getBoolean("enabled",true);}
    public static boolean isRunning(){return active!=null;}
    static void startIfEnabled(Context context){
        initialize(context);
        if(!isEnabled())return;
        Intent service=new Intent(context,NotificationService.class);
        if(Build.VERSION.SDK_INT>=26)context.startForegroundService(service);else context.startService(service);
    }
    public static void setEnabled(boolean enabled){
        if(application==null)throw new IllegalStateException("客户端尚未启动。");
        preferences().edit().putBoolean("enabled",enabled).commit();
        try{if(enabled)startIfEnabled(application);else application.stopService(new Intent(application,NotificationService.class));}
        catch(RuntimeException error){preferences().edit().putBoolean("enabled",false).commit();throw error;}
    }
    @Override public void onCreate(){
        super.onCreate();initialize(this);active=this;manager=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        if(Build.VERSION.SDK_INT>=26){
            NotificationChannel running=new NotificationChannel(RUNNING,"后台消息接收",NotificationManager.IMPORTANCE_LOW);running.setDescription("维持与自建服务端的加密消息连接");running.setShowBadge(false);manager.createNotificationChannel(running);
            if(manager.getNotificationChannel(MESSAGES)==null){
                NotificationChannel previous=manager.getNotificationChannel(LEGACY_MESSAGES);
                boolean userChoice=previous!=null&&(previous.getImportance()<NotificationManager.IMPORTANCE_DEFAULT||(Build.VERSION.SDK_INT>=29&&previous.hasUserSetImportance()));
                NotificationChannel messages=new NotificationChannel(MESSAGES,"新消息弹窗提醒",userChoice?previous.getImportance():NotificationManager.IMPORTANCE_HIGH);
                messages.setDescription("收到消息时显示系统横幅，不显示聊天正文");messages.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
                messages.enableVibration(previous==null||!userChoice||previous.shouldVibrate());
                if(previous!=null)messages.setSound(previous.getSound(),previous.getAudioAttributes());
                manager.createNotificationChannel(messages);
            }
        }
        startForeground(1,running());
        new Thread(()->{try{ClientRuntime.start(getApplicationContext());}catch(Exception e){stopSelf();}},"encrypted-receiver-start").start();
    }
    private PendingIntent open(String cid){return PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class).putExtra("conversation_id",cid).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP|Intent.FLAG_ACTIVITY_CLEAR_TOP),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);}
    private Notification.Builder builder(String channel){return Build.VERSION.SDK_INT>=26?new Notification.Builder(this,channel):new Notification.Builder(this);}
    private Notification running(){
        PendingIntent stop=PendingIntent.getService(this,1,new Intent(this,NotificationService.class).setAction("stop"),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        return builder(RUNNING).setSmallIcon(R.drawable.logo).setContentTitle("未言诗集").setContentText("加密消息服务正在运行").setContentIntent(open("")).setOngoing(true).setShowWhen(false).addAction(new Notification.Action.Builder(null,"停止后台接收",stop).build()).build();
    }
    public static void notifyMessage(String cid){
        NotificationService service=active;
        if(service==null||!isEnabled()||MainActivity.isForeground)return;
        if(Build.VERSION.SDK_INT>=33&&service.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return;
        service.unread++;
        Notification notification=service.builder(MESSAGES).setSmallIcon(R.drawable.logo).setContentTitle("未言诗集 · "+service.unread+" 条新消息")
            .setContentText("收到新的加密消息，打开客户端查看。").setContentIntent(service.open(cid)).setAutoCancel(true)
            .setVisibility(Notification.VISIBILITY_PRIVATE).setCategory(Notification.CATEGORY_MESSAGE).setPriority(Notification.PRIORITY_HIGH)
            .setDefaults(Notification.DEFAULT_ALL).setNumber(service.unread).build();
        service.manager.notify(2,notification);
    }
    public static void clearUnread(){NotificationService service=active;if(service!=null){service.unread=0;service.manager.cancel(2);}}
    public static void openMessageSettings(){if(application==null)return;Intent intent=Build.VERSION.SDK_INT>=26?new Intent(android.provider.Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS).putExtra(android.provider.Settings.EXTRA_APP_PACKAGE,application.getPackageName()).putExtra(android.provider.Settings.EXTRA_CHANNEL_ID,MESSAGES):new Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,android.net.Uri.parse("package:"+application.getPackageName()));application.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));}
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(intent!=null&&"stop".equals(intent.getAction()))preferences().edit().putBoolean("enabled",false).commit();
        if(!isEnabled()){clearUnread();stopForeground(true);stopSelf();return START_NOT_STICKY;}
        return START_STICKY;
    }
    @Override public IBinder onBind(Intent intent){return null;}
    @Override public void onDestroy(){clearUnread();if(active==this)active=null;super.onDestroy();}
}
