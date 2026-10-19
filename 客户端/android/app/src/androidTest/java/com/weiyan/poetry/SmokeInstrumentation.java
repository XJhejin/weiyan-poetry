package com.weiyan.poetry;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.view.MotionEvent;
import android.os.SystemClock;
import android.webkit.WebView;
import com.chaquo.python.Python;
import com.chaquo.python.PyObject;
import java.io.InputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Runs only from the separate test APK. No test accounts or switches in release. */
public class SmokeInstrumentation extends Instrumentation {
    private Bundle args;
    @Override public void onCreate(Bundle arguments){args=arguments;super.onCreate(arguments);start();}
    private WebView find(View v){if(v instanceof WebView)return (WebView)v;if(v instanceof ViewGroup){for(int i=0;i<((ViewGroup)v).getChildCount();i++){WebView w=find(((ViewGroup)v).getChildAt(i));if(w!=null)return w;}}return null;}
    private String js(WebView web,String source) throws Exception {
        CountDownLatch latch=new CountDownLatch(1);AtomicReference<String> value=new AtomicReference<>();
        runOnMainSync(()->web.evaluateJavascript(source,s->{value.set(s);latch.countDown();}));
        if(!latch.await(10,TimeUnit.SECONDS))throw new AssertionError("WebView did not respond");
        return value.get();
    }
    private void waitJs(WebView web,String condition) throws Exception {for(int i=0;i<150;i++){if("true".equals(js(web,condition)))return;Thread.sleep(200);}throw new AssertionError("UI condition timed out: "+condition+"; error="+js(web,"window.uiError || ''")+"; state="+js(web,"document.body.innerText.slice(0,2000)"));}
    private String shell(String command) throws Exception {
        try(android.os.ParcelFileDescriptor descriptor=getUiAutomation().executeShellCommand(command);
            InputStream input=new android.os.ParcelFileDescriptor.AutoCloseInputStream(descriptor)) {
            return new String(input.readAllBytes(),java.nio.charset.StandardCharsets.UTF_8);
        }
    }
    private void pinch(WebView web) throws Exception {
        int[] position=new int[2];runOnMainSync(()->web.getLocationOnScreen(position));
        float cx=position[0]+web.getWidth()/2f,cy=position[1]+web.getHeight()/2f;
        long down=SystemClock.uptimeMillis();
        MotionEvent.PointerProperties[] properties=new MotionEvent.PointerProperties[2];
        MotionEvent.PointerCoords[] points=new MotionEvent.PointerCoords[2];
        for(int i=0;i<2;i++){properties[i]=new MotionEvent.PointerProperties();properties[i].id=i;properties[i].toolType=MotionEvent.TOOL_TYPE_FINGER;points[i]=new MotionEvent.PointerCoords();points[i].y=cy;points[i].pressure=1;points[i].size=1;}
        points[0].x=cx-25;points[1].x=cx+25;
        for(int step=0;step<15;step++){
            int count=step==0||step==14?1:2;
            int action=step==0?MotionEvent.ACTION_DOWN:step==1?MotionEvent.ACTION_POINTER_DOWN|(1<<MotionEvent.ACTION_POINTER_INDEX_SHIFT):step==13?MotionEvent.ACTION_POINTER_UP|(1<<MotionEvent.ACTION_POINTER_INDEX_SHIFT):step==14?MotionEvent.ACTION_UP:MotionEvent.ACTION_MOVE;
            if(step>1&&step<13){points[0].x=cx-25-step*10;points[1].x=cx+25+step*10;}
            MotionEvent event=MotionEvent.obtain(down,SystemClock.uptimeMillis(),action,count,properties,points,0,0,1,1,0,0,android.view.InputDevice.SOURCE_TOUCHSCREEN,0);
            if(!getUiAutomation().injectInputEvent(event,true))throw new AssertionError("Real two-finger event injection failed");event.recycle();Thread.sleep(25);
        }
        Thread.sleep(250);
    }
    @Override public void onStart(){
        Bundle result=new Bundle();
        try {
            Intent intent=new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            Activity activity=startActivitySync(intent);
            WebView web=null;
            for(int i=0;i<200&&web==null;i++){AtomicReference<WebView> found=new AtomicReference<>();runOnMainSync(()->found.set(find(activity.getWindow().getDecorView())));web=found.get();if(web==null)Thread.sleep(250);}
            if(web==null)throw new AssertionError("Native application failed to start");
            waitJs(web,"typeof state !== 'undefined' && typeof chooseAttachment==='function' && typeof openGroupInfo==='function' && typeof openFriendRequests==='function'");
            waitJs(web,"document.querySelector('#boot').hidden && !document.querySelector('#auth').hidden");
            final WebView phoneWeb=web;
            AtomicReference<String> layoutError=new AtomicReference<>();
            runOnMainSync(()->{
                if(phoneWeb.getSettings().supportZoom()||phoneWeb.getSettings().getBuiltInZoomControls())layoutError.set("Phone pinch zoom must be disabled");
                android.view.WindowInsets insets=phoneWeb.getRootWindowInsets();
                if(insets==null){layoutError.set("Missing phone safe-area insets");return;}
                int safeTop=insets.getInsets(android.view.WindowInsets.Type.systemBars()|android.view.WindowInsets.Type.displayCutout()).top;
                int safeBottom=insets.getInsets(android.view.WindowInsets.Type.systemBars()|android.view.WindowInsets.Type.displayCutout()).bottom;
                int spacing=Math.round(8*phoneWeb.getResources().getDisplayMetrics().density);
                int[] position=new int[2];phoneWeb.getLocationOnScreen(position);
                int screenBottom=activity.getWindowManager().getCurrentWindowMetrics().getBounds().bottom;
                if(position[1]<safeTop+spacing)layoutError.set("Chat viewport overlaps status bar or display cutout");
                if(position[1]+phoneWeb.getHeight()>screenBottom-safeBottom)layoutError.set("Composer viewport overlaps navigation bar");
            });
            if(layoutError.get()!=null)throw new AssertionError(layoutError.get());
            shell("wm dismiss-keyguard");waitForIdleSync();
            android.view.accessibility.AccessibilityNodeInfo foregroundRoot=getUiAutomation().getRootInActiveWindow();
            if(foregroundRoot==null||!"com.weiyan.poetry".contentEquals(foregroundRoot.getPackageName()))throw new AssertionError("The phone app must be in foreground before the real pinch test");
            pinch(web);
            if(!"true".equals(js(web,"Math.abs(visualViewport.scale-1)<0.01 && document.querySelector('meta[name=viewport]').content.includes('user-scalable=no')")))throw new AssertionError("A real two-finger gesture zoomed the page");
            if(!NotificationService.batteryUnrestricted())throw new AssertionError("Test device did not grant background battery access");
            if("true".equals(args.getString("verifyDefaultTls","false"))){
                js(web,"fetch('/api/health').then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}).then(h=>{window.publicTlsOk=h.ok===true;}).catch(e=>{window.uiError='Public HTTPS: '+e.message;})");
                waitJs(web,"window.publicTlsOk===true");
            }
            String base=args.getString("server","http://10.0.2.2:23180");
            js(web,"fetch('/desktop/config').then(r=>r.json()).then(c=>{window.defaultCorrect=c.server_url==='https://156.225.28.175:23074';return post('/desktop/config',{server_url:'"+base+"'});}).then(()=>{window.configured=true;})");
            waitJs(web,"window.configured===true");
            if(!"true".equals(js(web,"window.defaultCorrect")))throw new AssertionError("Wrong released server default");
            js(web,"post('/api/register',{username:'android_ui_'+Date.now(),password:'Android-test-only-2288',nickname:'界面验证'}).then(()=>{window.uiStage='registered';return bootstrap({initial:true});}).then(()=>{connectEvents();window.loggedIn=true;}).catch(e=>{window.uiError=e.message})");
            waitJs(web,"window.loggedIn===true");
            js(web,"openProfile();let source=document.createElement('canvas');source.width=320;source.height=240;let pen=source.getContext('2d');pen.fillStyle='#d783a1';pen.fillRect(0,0,320,240);source.toBlob(blob=>{let data=new DataTransfer();data.items.add(new File([blob],'avatar.png',{type:'image/png'}));let input=document.querySelector('#profile-avatar-editor .avatar-file');input.files=data.files;input.dispatchEvent(new Event('change'));});");
            waitJs(web,"document.querySelector('#profile-avatar-editor .avatar-crop')?.hidden===false");
            js(web,"let scale=document.querySelector('#profile-avatar-editor .avatar-zoom');scale.value='1.5';scale.dispatchEvent(new Event('input'));document.querySelector('#profile-avatar-editor .avatar-apply').click();document.querySelector('#profile-form button[type=submit]').click();");
            waitJs(web,"state.me.avatar.startsWith('data:image/jpeg;base64,') && !document.querySelector('#modal').open");
            if(!"true".equals(js(web,"document.querySelector('#my-avatar .custom-avatar')!==null")))throw new AssertionError("Personal avatar did not synchronize");
            if(!"true".equals(js(web,"state.conversations.filter(c=>c.kind==='room').length===1 && state.conversations.find(c=>c.kind==='room').name==='-扉页-'")))throw new AssertionError("Room migration failed");
            js(web,"document.querySelector('#conversation-info').click();document.querySelector('#conversation-mute').click();");
            waitJs(web,"document.querySelector('#conversation-mute')?.getAttribute('aria-checked')==='true' && stored('muted',[]).includes('room:lobby')");
            js(web,"closeModal();document.querySelector('#conversation-info').click();");
            waitJs(web,"document.querySelector('#conversation-mute')?.getAttribute('aria-checked')==='true'");
            js(web,"document.querySelector('#conversation-mute').click();");
            waitJs(web,"document.querySelector('#conversation-mute')?.getAttribute('aria-checked')==='false'");
            js(web,"closeModal();");
            js(web,"openCreateGroup();document.querySelector('#new-group-name').value='手机群聊';document.querySelector('#create-group-form button[type=submit]').click();");
            waitJs(web,"state.conversations.some(c=>c.kind==='group'&&c.name==='手机群聊')");
            waitJs(web,"document.querySelector('#edit-group')!==null");
            js(web,"document.querySelector('#conversation-mute').click();");
            waitJs(web,"document.querySelector('#conversation-mute')?.getAttribute('aria-checked')==='true' && stored('muted',[]).includes(state.active)");
            js(web,"closeModal();document.querySelector('#message-input').value='安卓加密消息';sendMessage();");
            waitJs(web,"[...state.messages.values()].some(m=>m.body==='安卓加密消息'&&m.encrypted)");
            js(web,"setMessageQuote([...state.messages.values()].find(m=>m.body==='安卓加密消息'));document.querySelector('#message-input').value='引用回复';updateInput();sendMessage();");
            waitJs(web,"[...state.messages.values()].some(m=>m.body==='引用回复'&&m.quote?.excerpt==='安卓加密消息') && document.querySelector('.message-quote')!==null");
            if(!"true".equals(js(web,"document.querySelector('#file-button')!==null && document.querySelector('#video-button')!==null && document.querySelector('#encryption-info')!==null")))throw new AssertionError("Phone controls missing");
            js(web,"window.largeUiFile=new File([new Uint8Array([0,0,0,24,102,116,121,112,105,115,111,109]),new Uint8Array(21*1024*1024)],'phone-large.mp4',{type:'video/mp4'});chooseAttachment(window.largeUiFile).then(()=>window.largeUiReady=true).catch(e=>window.uiError=e.message);");
            waitJs(web,"window.largeUiReady===true && state.fileAttachment?.size>20*1024*1024 && document.querySelector('.attachment-video-preview')!==null && !state.filePreparing");
            if(!"true".equals(js(web,"MAX_ATTACHMENT_SIZE===10*1024**3 && fileSize(MAX_ATTACHMENT_SIZE)==='10.00 GB'")))throw new AssertionError("Phone attachment limit is not 10 GiB");
            js(web,"sendMessage();");
            waitJs(web,"[...state.messages.values()].some(m=>m.attachment?.name==='phone-large.mp4'&&m.encrypted) && document.querySelector('.message-video')!==null");
            js(web,"window.weiyanSetMessageNotices(true);window.popupOldActive=state.active;window.popupOldMessages=state.messages;state.active='test-other-conversation';state.messages=new Map();window.popupCid='room:lobby';window.popupTestMessage={id:9000001,client_id:'popup-only-test',conversation_id:window.popupCid,sender:{id:'popup-peer',nickname:'提醒验证',avatar:'flower'},body:'popup-private-body-3348',created_at:new Date().toISOString()};receiveMessage(window.popupTestMessage);");
            waitJs(web,"document.querySelector('#message-popup').hidden===false");
            if(!"true".equals(js(web,"!document.querySelector('#message-popup').textContent.includes('popup-private-body')")))throw new AssertionError("Foreground banner revealed plaintext");
            js(web,"document.querySelector('.message-popup-close').click();let oldMuted=stored('muted',[]);store('muted',[...oldMuted,window.popupCid]);receiveMessage({...window.popupTestMessage,id:9000002});window.popupWasSuppressed=document.querySelector('#message-popup').hidden;store('muted',oldMuted);state.active=window.popupOldActive;state.messages=window.popupOldMessages;renderMessages();");
            if(!"true".equals(js(web,"window.popupWasSuppressed")))throw new AssertionError("Muted conversation triggered foreground banner");
            js(web,"window.mentionPreviousMuted=stored('muted',[]);store('muted',[...window.mentionPreviousMuted,'room:lobby']);window.mentionPreviousActive=state.active;state.active='mention-other';receiveMessage({...window.popupTestMessage,id:9000003,client_id:'popup-mention',body:'@我',mentions:[{user_id:state.me.id,start:0,length:2}]});window.mentionBypass=!document.querySelector('#message-popup').hidden;document.querySelector('.message-popup-close').click();receiveMessage({...window.popupTestMessage,id:9000004,client_id:'popup-history',body:'@我',mentions:[{user_id:state.me.id,start:0,length:2}],history_sync:true});window.historySilent=document.querySelector('#message-popup').hidden;store('muted',window.mentionPreviousMuted);state.active=window.mentionPreviousActive;");
            if(!"true".equals(js(web,"window.mentionBypass&&window.historySilent")))throw new AssertionError("Mention override or silent history import failed");
            js(web,"post('/api/recall',{id:[...state.messages.values()].find(m=>m.body==='安卓加密消息').id,client_id:[...state.messages.values()].find(m=>m.body==='安卓加密消息').client_id}).then(r=>receiveMessage(r.message)).catch(e=>window.uiError=e.message)");
            waitJs(web,"[...state.messages.values()].some(m=>m.recalled) && document.querySelector('.recalled-message')!==null");
            try(InputStream in=getContext().getAssets().open("smoke.py")){
                String code=new String(in.readAllBytes(),java.nio.charset.StandardCharsets.UTF_8);
                PyObject globals=Python.getInstance().getModule("builtins").callAttr("dict");
                globals.callAttr("__setitem__","server_url",base);globals.callAttr("__setitem__","test_profile",new java.io.File(getTargetContext().getNoBackupFilesDir(),"instrumentation").getPath());
                Python.getInstance().getModule("builtins").callAttr("exec",code,globals);
                if(!"PASS".equals(globals.callAttr("__getitem__","smoke_result").toString()))throw new AssertionError("Native crypto smoke failed");
                runOnMainSync(()->activity.moveTaskToBack(true));
                Thread.sleep(750);
                Python.getInstance().getModule("builtins").callAttr("exec",
                    "from mobile import _gateway\n_gateway.background.activate(b, bob.call('/api/bootstrap'))\nb.active_sockets=1\na.send({'conversation_id':cid,'body':'notification-private-body-6628','client_id':'notify-'+secrets.token_hex(8)})",globals);
                for(int i=0;i<40&&!NotificationService.receiverAwake();i++)Thread.sleep(100);
                if(!NotificationService.receiverAwake())throw new AssertionError("Logged-in background receiver did not keep the CPU available");
                android.app.NotificationManager manager=(android.app.NotificationManager)getTargetContext().getSystemService(android.content.Context.NOTIFICATION_SERVICE);
                android.app.NotificationChannel channel=manager.getNotificationChannel("encrypted_messages_popup_v2");
                if(channel==null||channel.getImportance()!=android.app.NotificationManager.IMPORTANCE_HIGH)throw new AssertionError("New message channel must allow heads-up banners");
                boolean notified=false;
                for(int i=0;i<60&&!notified;i++){
                    for(android.service.notification.StatusBarNotification n:manager.getActiveNotifications())if(n.getId()==2){
                        String content=n.getNotification().extras.toString();
                        if(content.contains("notification-private-body"))throw new AssertionError("Notification revealed message plaintext");
                        notified=true;
                    }
                    if(!notified)Thread.sleep(250);
                }
                if(!notified)throw new AssertionError("No encrypted message notification while Activity was in background");
                android.app.PendingIntent serviceOpen=null,messageOpen=null;
                for(android.service.notification.StatusBarNotification n:manager.getActiveNotifications()){
                    if(n.getId()==1)serviceOpen=n.getNotification().contentIntent;
                    if(n.getId()==2)messageOpen=n.getNotification().contentIntent;
                }
                if(serviceOpen==null||messageOpen==null||serviceOpen.equals(messageOpen))throw new AssertionError("Service notification must not overwrite the message conversation target");
                for(android.service.notification.StatusBarNotification n:manager.getActiveNotifications())if(n.getId()==1){
                    CharSequence text=n.getNotification().extras.getCharSequence(android.app.Notification.EXTRA_TEXT);
                    if(text!=null&&text.toString().contains("加密消息服务正在运行"))throw new AssertionError("Legacy service text is still shown");
                }
                String nativeCid=globals.callAttr("__getitem__","cid").toString();
                NotificationService.notifyMessage(nativeCid,"native-dedup-check");
                Thread.sleep(150);
                int noticeCount=0;
                for(android.service.notification.StatusBarNotification n:manager.getActiveNotifications())if(n.getId()==2)noticeCount=n.getNotification().number;
                if(noticeCount<2)throw new AssertionError("Consecutive incoming messages did not update notification count");
                NotificationService.notifyMessage(nativeCid,"native-dedup-check");
                Thread.sleep(150);
                for(android.service.notification.StatusBarNotification n:manager.getActiveNotifications())if(n.getId()==2&&n.getNotification().number!=noticeCount)throw new AssertionError("Duplicate native notification changed unread count");
                NotificationService.clearUnread();
                Python.getInstance().getModule("builtins").callAttr("exec",
                    "b.vault.put('ui','muted',['room:lobby'])\nmuted_sent=a.send({'conversation_id':'room:lobby','body':'muted-room-marker','client_id':'mute-'+secrets.token_hex(8)})",globals);
                boolean receivedMuted=false;
                for(int i=0;i<60&&!receivedMuted;i++){
                    receivedMuted=Python.getInstance().getModule("builtins").callAttr("eval","any(m['body']=='muted-room-marker' for m in b.vault.messages('room:lobby'))",globals).toBoolean();
                    if(!receivedMuted)Thread.sleep(250);
                }
                if(!receivedMuted)throw new AssertionError("Muted room message was not received and stored");
                for(android.service.notification.StatusBarNotification n:manager.getActiveNotifications())if(n.getId()==2)throw new AssertionError("Muted room triggered a notification");
                Python.getInstance().getModule("builtins").callAttr("exec",
                    "mention_id='mention-'+secrets.token_hex(8)\na.send({'conversation_id':'room:lobby','body':'@target','client_id':mention_id,'mentions':[{'user_id':b.me['id'],'start':0,'length':7}]})",globals);
                boolean mentionNotified=false;
                for(int i=0;i<60&&!mentionNotified;i++){for(android.service.notification.StatusBarNotification n:manager.getActiveNotifications())if(n.getId()==2)mentionNotified=true;if(!mentionNotified)Thread.sleep(250);}
                if(!mentionNotified)throw new AssertionError("Explicit mention did not bypass room mute in native background notifications");
                NotificationService.clearUnread();
                getTargetContext().startService(new Intent(getTargetContext(),NotificationService.class).setAction("stop"));
                for(int i=0;i<40&&NotificationService.isRunning();i++)Thread.sleep(100);
                if(NotificationService.isEnabled()||NotificationService.isRunning())throw new AssertionError("Explicit stop must disable background reception");
                if(NotificationService.receiverAwake())throw new AssertionError("Explicit stop leaked the receiver wake lock");
                new BackgroundReceiver().onReceive(getTargetContext(),new Intent(Intent.ACTION_MY_PACKAGE_REPLACED));
                Thread.sleep(250);
                if(NotificationService.isRunning())throw new AssertionError("Upgrade receiver ignored explicit stop preference");
                getTargetContext().startActivity(new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP|Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
                Thread.sleep(750);
                NotificationService.setEnabled(true);
                for(int i=0;i<40&&!NotificationService.isRunning();i++)Thread.sleep(100);
                if(!NotificationService.isEnabled()||!NotificationService.isRunning())throw new AssertionError("User could not resume background reception");
                getTargetContext().startActivity(new Intent(getTargetContext(),MainActivity.class).putExtra("conversation_id","room:lobby").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP|Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
                waitJs(web,"state.active==='room:lobby'");
                for(int i=0;i<40&&NotificationService.receiverAwake();i++)Thread.sleep(100);
                if(NotificationService.receiverAwake())throw new AssertionError("Foreground interface should release the receiver wake lock");
                shell("dumpsys deviceidle whitelist -com.weiyan.poetry");
                android.content.IntentFilter requestFilter=new android.content.IntentFilter(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);requestFilter.addDataScheme("package");
                ActivityMonitor permissionMonitor=addMonitor(requestFilter,null,true);
                runOnMainSync(MainActivity::openBackgroundSettings);
                boolean clicked=false;
                for(int i=0;i<30&&!clicked;i++){
                    android.view.accessibility.AccessibilityNodeInfo root=getUiAutomation().getRootInActiveWindow();
                    if(root!=null){for(android.view.accessibility.AccessibilityNodeInfo node:root.findAccessibilityNodeInfosByText("允许后台运行"))if(node.isClickable())clicked=node.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK);}
                    if(!clicked)Thread.sleep(100);
                }
                for(int i=0;i<30&&permissionMonitor.getHits()==0;i++)Thread.sleep(100);
                if(!clicked||permissionMonitor.getHits()!=1)throw new AssertionError("Background guide did not request the system battery exemption");
                removeMonitor(permissionMonitor);
                shell("dumpsys deviceidle whitelist +com.weiyan.poetry");
                if(!NotificationService.batteryUnrestricted())throw new AssertionError("Background permission status did not refresh");
                runOnMainSync(activity::finishAndRemoveTask);
                for(int i=0;i<40&&!activity.isDestroyed();i++)Thread.sleep(100);
                if(!activity.isDestroyed()||MainActivity.isForeground||!NotificationService.isRunning())throw new AssertionError("Closing the interface also stopped encrypted reception");
                Python.getInstance().getModule("builtins").callAttr("exec","_gateway.background.activate(b, bob.call('/api/bootstrap'))",globals);
                shell("input keyevent 223");shell("dumpsys battery unplug");
                try {
                    shell("dumpsys deviceidle enable");
                    String forced=shell("dumpsys deviceidle force-idle deep");
                    String idleState="";
                    for(int i=0;i<40;i++){idleState=shell("dumpsys deviceidle get deep");if(idleState.contains("IDLE"))break;Thread.sleep(100);}
                    if(!idleState.contains("IDLE"))throw new AssertionError("Device did not enter Doze: "+forced+" / "+idleState);
                    NotificationService.clearUnread();
                    Python.getInstance().getModule("builtins").callAttr("exec",
                        "closed_id='closed-'+secrets.token_hex(8)\na.send({'conversation_id':cid,'body':'closed-private-body-4492','client_id':closed_id})",globals);
                    boolean receivedClosed=false,notifiedClosed=false;
                    for(int i=0;i<80&&(!receivedClosed||!notifiedClosed);i++){
                        receivedClosed=Python.getInstance().getModule("builtins").callAttr("eval","any(m['client_id']==closed_id for m in b.vault.messages(cid))",globals).toBoolean();
                        for(android.service.notification.StatusBarNotification notification:manager.getActiveNotifications())if(notification.getId()==2){
                            if(notification.getNotification().extras.toString().contains("closed-private-body"))throw new AssertionError("Closed-interface notification revealed plaintext");
                            notifiedClosed=true;
                        }
                        if(!receivedClosed||!notifiedClosed)Thread.sleep(250);
                    }
                    if(!receivedClosed||!notifiedClosed||!NotificationService.receiverAwake())throw new AssertionError("Closed interface failed to receive and notify during Doze");
                } finally {shell("dumpsys deviceidle unforce");shell("dumpsys battery reset");shell("input keyevent 224");}
            }
            result.putString("WeiyanSmoke","PASS");finish(Activity.RESULT_OK,result);
        }catch(Throwable e){result.putString("WeiyanSmoke","FAIL: "+android.util.Log.getStackTraceString(e));finish(Activity.RESULT_CANCELED,result);}
    }
}
