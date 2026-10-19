package com.weiyan.poetry;

import android.Manifest;
import android.app.*;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import java.util.LinkedHashSet;

/** Message-only notifications. This class is deliberately not an Android Service.
 * Process-death delivery requires an activated vendor push provider; none is
 * silently enabled or represented as configured by this implementation.
 */
public final class MessageNotifications {
    private static Context application;
    private static NotificationManager manager;
    private static final Handler handler=new Handler(Looper.getMainLooper());
    private static final String MESSAGES="encrypted_messages_popup_v2";
    private static final LinkedHashSet<String> notified=new LinkedHashSet<>();
    private static int unread;
    private static long lastAlert=-1;

    static synchronized void initialize(Context context){
        if(application!=null)return;
        application=context.getApplicationContext();
        manager=(NotificationManager)application.getSystemService(Context.NOTIFICATION_SERVICE);
        unread=preferences().getInt("unread",0);
        // Remove the old ongoing notification, including after an in-place upgrade.
        manager.cancel(1);
        if(Build.VERSION.SDK_INT>=26){
            manager.deleteNotificationChannel("encrypted_receiver");
            manager.deleteNotificationChannel("encrypted_receiver_no_badge_v2");
            if(manager.getNotificationChannel(MESSAGES)==null){
                NotificationChannel previous=manager.getNotificationChannel("encrypted_messages");
                boolean userChoice=previous!=null&&(previous.getImportance()<NotificationManager.IMPORTANCE_DEFAULT||(Build.VERSION.SDK_INT>=29&&previous.hasUserSetImportance()));
                NotificationChannel channel=new NotificationChannel(MESSAGES,"新消息弹窗提醒",userChoice?previous.getImportance():NotificationManager.IMPORTANCE_HIGH);
                channel.setDescription("仅有新消息时提醒，不显示聊天正文");
                channel.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
                channel.setShowBadge(true);
                channel.enableVibration(previous==null||!userChoice||previous.shouldVibrate());
                if(previous!=null)channel.setSound(previous.getSound(),previous.getAudioAttributes());
                manager.createNotificationChannel(channel);
            }
        }
    }
    private static SharedPreferences preferences(){return application.getSharedPreferences("background-reception",Context.MODE_PRIVATE);}
    public static boolean isEnabled(){return application!=null&&preferences().getBoolean("enabled",true);}
    public static boolean isRunning(){return application!=null&&isEnabled();}
    public static boolean batteryUnrestricted(){return application!=null&&BackgroundAccess.batteryUnrestricted(application);}
    public static void setSessionActive(boolean value){if(!value)clearUnread();}
    public static void refreshReception(){}
    public static void setEnabled(boolean enabled){
        if(application==null)throw new IllegalStateException("客户端尚未启动。");
        preferences().edit().putBoolean("enabled",enabled).commit();
        if(!enabled)clearUnread();
    }
    public static void notifyMessage(String cid){notifyMessage(cid,"");}
    public static void notifyMessage(String cid,String messageId){handler.post(()->showMessage(cid,messageId));}
    private static PendingIntent open(String cid){
        return PendingIntent.getActivity(application,2,new Intent(application,MainActivity.class).putExtra("conversation_id",cid)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP|Intent.FLAG_ACTIVITY_CLEAR_TOP),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
    }
    private static void showMessage(String cid,String messageId){
        if(!isEnabled()||MainActivity.isForeground)return;
        if(Build.VERSION.SDK_INT>=33&&application.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return;
        if(!messageId.isEmpty()){
            if(!notified.add(cid+"\n"+messageId))return;
            if(notified.size()>512)notified.remove(notified.iterator().next());
        }
        long now=SystemClock.elapsedRealtime();
        boolean alert=lastAlert<0||now-lastAlert>=3000;
        if(alert)lastAlert=now;
        unread++;preferences().edit().putInt("unread",unread).apply();
        Notification.Builder builder=Build.VERSION.SDK_INT>=26?new Notification.Builder(application,MESSAGES):new Notification.Builder(application);
        Notification notice=builder.setSmallIcon(R.drawable.logo).setContentTitle("未言诗集 · "+unread+" 条新消息")
            .setContentText("收到新的加密消息，打开客户端查看。").setContentIntent(open(cid)).setAutoCancel(true)
            .setVisibility(Notification.VISIBILITY_PRIVATE).setCategory(Notification.CATEGORY_MESSAGE)
            .setPriority(Notification.PRIORITY_HIGH).setDefaults(Notification.DEFAULT_ALL).setOnlyAlertOnce(!alert).setNumber(unread).build();
        manager.notify(2,notice);
    }
    public static void clearUnread(){
        if(application==null)return;
        handler.post(()->{unread=0;preferences().edit().putInt("unread",0).apply();manager.cancel(2);manager.cancel(1);});
    }
    public static void openMessageSettings(){
        if(application==null)return;
        Intent intent=Build.VERSION.SDK_INT>=26?new Intent(android.provider.Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
            .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE,application.getPackageName())
            .putExtra(android.provider.Settings.EXTRA_CHANNEL_ID,MESSAGES):
            new Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,android.net.Uri.parse("package:"+application.getPackageName()));
        application.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    }
}
