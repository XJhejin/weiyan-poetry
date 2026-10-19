package com.weiyan.poetry;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;
import android.widget.Toast;

/** System permission requests always remain visible and under the user's control. */
final class BackgroundAccess {
    static final int NOTIFICATIONS = 104;
    private final Activity activity;
    private boolean awaitingNotifications;

    BackgroundAccess(Activity activity) { this.activity = activity; }

    static boolean batteryUnrestricted(Context context) {
        PowerManager power = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
        return power.isIgnoringBatteryOptimizations(context.getPackageName());
    }

    void request(boolean explicit) {
        if (activity.isFinishing() || activity.isDestroyed() || !NotificationService.isEnabled()) return;
        SharedPreferences preferences = activity.getSharedPreferences("background-reception", Context.MODE_PRIVATE);
        if (!explicit && preferences.getInt("permission-guide-version", 0) >= 1) return;
        preferences.edit().putInt("permission-guide-version", 1).apply();
        if (Build.VERSION.SDK_INT >= 33 && activity.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            awaitingNotifications = true;
            activity.requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFICATIONS);
        } else explainBatteryAccess(explicit);
    }

    void onPermissionResult(int request) {
        if (request == NOTIFICATIONS && awaitingNotifications) {
            awaitingNotifications = false;
            explainBatteryAccess(false);
        }
    }

    private void explainBatteryAccess(boolean explicit) {
        if (activity.isFinishing() || activity.isDestroyed() || !NotificationService.isEnabled()) return;
        if (batteryUnrestricted(activity)) {
            if (explicit) openApplicationSettings();
            return;
        }
        new AlertDialog.Builder(activity).setTitle("允许后台接收消息")
            .setMessage("允许不受电池优化限制后，关闭界面或息屏时仍可接收加密消息。后台接收可能增加耗电，可随时在设置中关闭。部分手机还需在应用设置或手机管家中允许自启动与后台运行。")
            .setNegativeButton("暂不设置", null)
            .setPositiveButton("允许后台运行", (dialog, which) -> requestBatteryAccess()).show();
    }

    private void requestBatteryAccess() {
        try {
            activity.startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                Uri.parse("package:" + activity.getPackageName())));
        } catch (RuntimeException unavailable) {
            try { activity.startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)); }
            catch (RuntimeException error) { openApplicationSettings(); }
        }
    }

    void openApplicationSettings() {
        try { activity.startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + activity.getPackageName()))); }
        catch (RuntimeException error) { Toast.makeText(activity,"请在系统设置中打开未言诗集的电池与后台运行设置。",Toast.LENGTH_LONG).show(); }
    }
}
