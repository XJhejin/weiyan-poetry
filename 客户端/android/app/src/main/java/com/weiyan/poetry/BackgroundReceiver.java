package com.weiyan.poetry;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Restore the user's enabled receiver after reboot or an in-place upgrade. */
public class BackgroundReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context,Intent intent){
        String action=intent.getAction();
        if(!Intent.ACTION_BOOT_COMPLETED.equals(action)&&!Intent.ACTION_MY_PACKAGE_REPLACED.equals(action))return;
        if(!context.getSharedPreferences("background-reception",Context.MODE_PRIVATE).getBoolean("initialized",false))return;
        try{NotificationService.startIfEnabled(context);}
        catch(RuntimeException error){android.util.Log.w("WeiyanBackground","System deferred background reception; reopen the app.");}
    }
}
