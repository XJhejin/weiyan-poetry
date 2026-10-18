package com.weiyan.poetry;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
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
    @Override public void onStart(){
        Bundle result=new Bundle();
        try {
            Intent intent=new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            Activity activity=startActivitySync(intent);
            WebView web=null;
            for(int i=0;i<200&&web==null;i++){AtomicReference<WebView> found=new AtomicReference<>();runOnMainSync(()->found.set(find(activity.getWindow().getDecorView())));web=found.get();if(web==null)Thread.sleep(250);}
            if(web==null)throw new AssertionError("Native application failed to start");
            waitJs(web,"typeof state !== 'undefined' && typeof chooseAttachment==='function' && typeof openGroupInfo==='function' && typeof openFriendRequests==='function'");
            String base=args.getString("server","http://10.0.2.2:23180");
            js(web,"fetch('/desktop/config').then(r=>r.json()).then(c=>{window.defaultCorrect=c.server_url==='http://113.219.237.106:23074';return post('/desktop/config',{server_url:'"+base+"'});}).then(()=>{window.configured=true;})");
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
            js(web,"openCreateGroup();document.querySelector('#new-group-name').value='手机群聊';document.querySelector('#create-group-form button[type=submit]').click();");
            waitJs(web,"state.conversations.some(c=>c.kind==='group'&&c.name==='手机群聊')");
            waitJs(web,"document.querySelector('#edit-group')!==null");
            js(web,"closeModal();document.querySelector('#message-input').value='安卓加密消息';sendMessage();");
            waitJs(web,"[...state.messages.values()].some(m=>m.body==='安卓加密消息'&&m.encrypted)");
            if(!"true".equals(js(web,"document.querySelector('#file-button')!==null && document.querySelector('#video-button')!==null && document.querySelector('#encryption-info')!==null")))throw new AssertionError("Phone controls missing");
            try(InputStream in=getContext().getAssets().open("smoke.py")){
                String code=new String(in.readAllBytes(),java.nio.charset.StandardCharsets.UTF_8);
                PyObject globals=Python.getInstance().getModule("builtins").callAttr("dict");
                globals.callAttr("__setitem__","server_url",base);globals.callAttr("__setitem__","test_profile",new java.io.File(getTargetContext().getNoBackupFilesDir(),"instrumentation").getPath());
                Python.getInstance().getModule("builtins").callAttr("exec",code,globals);
                if(!"PASS".equals(globals.callAttr("__getitem__","smoke_result").toString()))throw new AssertionError("Native crypto smoke failed");
                runOnMainSync(()->activity.moveTaskToBack(true));
                Thread.sleep(750);
                Python.getInstance().getModule("builtins").callAttr("exec",
                    "from mobile import _gateway\n_gateway.background.activate(b, bob.call('/api/bootstrap'))\na.send({'conversation_id':cid,'body':'notification-private-body-6628','client_id':'notify-'+secrets.token_hex(8)})",globals);
                android.app.NotificationManager manager=(android.app.NotificationManager)getTargetContext().getSystemService(android.content.Context.NOTIFICATION_SERVICE);
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
                getTargetContext().startActivity(new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP|Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
            }
            result.putString("WeiyanSmoke","PASS");finish(Activity.RESULT_OK,result);
        }catch(Throwable e){result.putString("WeiyanSmoke","FAIL: "+android.util.Log.getStackTraceString(e));finish(Activity.RESULT_CANCELED,result);}
    }
}
