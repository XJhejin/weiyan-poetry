package com.weiyan.poetry;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

/** Grants the system installer temporary read access to the verified APK only. */
public final class UpdateProvider extends ContentProvider {
    @Override public boolean onCreate(){return true;}
    private File file(Uri uri) throws FileNotFoundException {
        try {
            File root=new File(getContext().getNoBackupFilesDir(),"client/updates").getCanonicalFile();
            String path=uri.getQueryParameter("path");
            if(path==null)throw new FileNotFoundException();
            File file=new File(path).getCanonicalFile();
            if(!"/apk".equals(uri.getPath())||!file.getPath().startsWith(root.getPath()+File.separator)||!file.getName().endsWith(".apk")||!file.isFile())throw new FileNotFoundException();
            return file;
        }catch(java.io.IOException e){throw new FileNotFoundException("更新文件不可用。");}
    }
    @Override public ParcelFileDescriptor openFile(Uri uri,String mode) throws FileNotFoundException {if(!"r".equals(mode))throw new FileNotFoundException();return ParcelFileDescriptor.open(file(uri),ParcelFileDescriptor.MODE_READ_ONLY);}
    @Override public String getType(Uri uri){return "application/vnd.android.package-archive";}
    @Override public Cursor query(Uri uri,String[] projection,String selection,String[] args,String sort){try{File file=file(uri);MatrixCursor c=new MatrixCursor(new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE});c.addRow(new Object[]{file.getName(),file.length()});return c;}catch(FileNotFoundException e){return null;}}
    @Override public Uri insert(Uri uri,ContentValues values){throw new UnsupportedOperationException();}
    @Override public int update(Uri uri,ContentValues values,String selection,String[] args){throw new UnsupportedOperationException();}
    @Override public int delete(Uri uri,String selection,String[] args){throw new UnsupportedOperationException();}
}
