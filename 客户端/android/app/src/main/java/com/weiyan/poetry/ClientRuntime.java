package com.weiyan.poetry;

import android.content.Context;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import java.io.*;

final class ClientRuntime {
    private static String url;
    static synchronized String start(Context context) throws IOException {
        NotificationService.initialize(context);
        if(url!=null)return url;
        File ui=new File(context.getNoBackupFilesDir(),"ui");copyAssets(context,"ui",ui);
        if(!Python.isStarted())Python.start(new AndroidPlatform(context.getApplicationContext()));
        url=Python.getInstance().getModule("mobile").callAttr("start",new File(context.getNoBackupFilesDir(),"client").getPath(),ui.getPath()).toString();
        return url;
    }
    private static void copyAssets(Context context,String asset,File dest) throws IOException {
        String[] names=context.getAssets().list(asset);
        if(names!=null&&names.length>0){if(!dest.isDirectory()&&!dest.mkdirs())throw new IOException("无法准备本机界面");for(String name:names)copyAssets(context,asset+"/"+name,new File(dest,name));}
        else try(InputStream in=context.getAssets().open(asset);OutputStream out=new FileOutputStream(dest)){byte[] buffer=new byte[65536];int n;while((n=in.read(buffer))!=-1)out.write(buffer,0,n);}
    }
}
