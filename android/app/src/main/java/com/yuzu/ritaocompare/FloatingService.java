package com.yuzu.ritaocompare;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.IBinder;
import android.util.DisplayMetrics;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/** A draggable bubble over any app. Tap it to expand a floating WebView with the comparison panel. */
public class FloatingService extends Service {

    private static final String CHANNEL = "ritao_compare_fg";
    private WindowManager wm;
    private View bubble;
    private LinearLayout panel;
    private boolean expanded = false;

    @Override
    public void onCreate() {
        super.onCreate();
        wm = (WindowManager) getSystemService(Context.WINDOW_SERVICE);

        // Android 14+ requires a declared foregroundServiceType; some OEM ROMs
        // reject startForeground for other reasons too. Never let that crash
        // the app -- the overlay works fine as a plain service.
        try {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "比价悬浮球",
                    NotificationManager.IMPORTANCE_LOW);
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) nm.createNotificationChannel(ch);
            Notification n = new Notification.Builder(this, CHANNEL)
                    .setContentTitle("日淘比价助手")
                    .setContentText("悬浮球已开启，点一下查价格")
                    .setSmallIcon(android.R.drawable.ic_menu_search)
                    .setOngoing(true)
                    .build();
            startForeground(1, n);
        } catch (Throwable ignored) {
            // Fall back to a normal service. We are started with startService(),
            // so no 5-second foreground-service timeout applies.
        }
        try {
            addBubble();
        } catch (Throwable ignored) {
            // Missing overlay permission or OEM restriction: keep the process alive.
        }
    }

    private void addBubble() {
        TextView tv = new TextView(this);
        tv.setText("查价格");
        tv.setTextColor(Color.parseColor("#1A1206"));
        tv.setTextSize(13f);
        tv.setPadding(28, 16, 28, 16);
        GradientDrawable gd = new GradientDrawable();
        gd.setColor(Color.parseColor("#E9A23B"));
        gd.setCornerRadius(60f);
        gd.setStroke(2, Color.parseColor("#F3BC61"));
        tv.setBackground(gd);
        bubble = tv;

        final WindowManager.LayoutParams p = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                android.graphics.PixelFormat.TRANSLUCENT);
        p.gravity = Gravity.TOP | Gravity.END;
        p.x = 16;
        p.y = 320;

        tv.setOnTouchListener(new View.OnTouchListener() {
            float downX = 0, downY = 0; int startX = 0, startY = 0; boolean moved = false;

            @Override
            public boolean onTouch(View v, MotionEvent e) {
                switch (e.getAction()) {
                    case MotionEvent.ACTION_DOWN:
                        downX = e.getRawX(); downY = e.getRawY();
                        startX = p.x; startY = p.y; moved = false;
                        return true;
                    case MotionEvent.ACTION_MOVE:
                        float dx = e.getRawX() - downX, dy = e.getRawY() - downY;
                        if (Math.abs(dx) > 8 || Math.abs(dy) > 8) moved = true;
                        p.x = (int) (startX - dx);
                        p.y = (int) (startY + dy);
                        try { wm.updateViewLayout(bubble, p); } catch (Exception ignored) {}
                        return true;
                    case MotionEvent.ACTION_UP:
                        if (!moved) togglePanel();
                        return true;
                    default:
                        return false;
                }
            }
        });
        try { wm.addView(bubble, p); } catch (Exception e) {
            Toast.makeText(this, "悬浮窗权限未开启", Toast.LENGTH_LONG).show();
        }
    }

    private void togglePanel() {
        if (expanded) { closePanel(); return; }
        String kw = CaptureService.lastKeyword;
        if (kw == null || kw.trim().isEmpty()) {
            Toast.makeText(this, "没读到商品名，请在日淘市集里点「分享 → 日淘比价助手」，或在面板里手动粘贴", Toast.LENGTH_LONG).show();
        }

        DisplayMetrics dm = new DisplayMetrics();
        wm.getDefaultDisplay().getMetrics(dm);
        // LayoutParams width/height are in PHYSICAL pixels. On a 3x-density
        // phone a fixed 900px window is only ~300 CSS px, which made the web
        // panel look like a narrow strip. Match the screen width instead.
        int w = WindowManager.LayoutParams.MATCH_PARENT;
        int h = (int) (dm.heightPixels * 0.86);

        panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setBackgroundColor(Color.parseColor("#171B22"));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.parseColor("#171B22"));
        bg.setCornerRadius(18f);
        bg.setStroke(2, Color.parseColor("#3A4350"));
        panel.setBackground(bg);

        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setPadding(18, 12, 12, 12);
        bar.setBackgroundColor(Color.parseColor("#20262F"));
        TextView title = new TextView(this);
        title.setText("日淘比价助手");
        title.setTextColor(Color.parseColor("#E8ECF2"));
        title.setTextSize(14f);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        title.setLayoutParams(lp);
        Button openBrowser = new Button(this);
        openBrowser.setText("全屏");
        openBrowser.setTextColor(Color.parseColor("#E9A23B"));
        openBrowser.setBackgroundColor(Color.TRANSPARENT);
        openBrowser.setOnClickListener(v -> {
            String url = Prefs.panelUrl(this, CaptureService.lastKeyword, CaptureService.lastSourceUrl);
            try {
                Intent i = new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(i);
            } catch (Exception ignored) {}
        });
        Button close = new Button(this);
        close.setText("关闭");
        close.setTextColor(Color.parseColor("#E8ECF2"));
        close.setBackgroundColor(Color.TRANSPARENT);
        close.setOnClickListener(v -> closePanel());
        bar.addView(title);
        bar.addView(openBrowser);
        bar.addView(close);
        panel.addView(bar);

        WebView web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#171B22"));
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setLoadWithOverviewMode(true);
        web.getSettings().setUseWideViewPort(true);
        web.setWebViewClient(new WebViewClient());
        web.setLayoutParams(new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        web.loadUrl(Prefs.panelUrl(this, kw, CaptureService.lastSourceUrl));
        panel.addView(web);

        WindowManager.LayoutParams pp = new WindowManager.LayoutParams(
                w, h,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                0,
                android.graphics.PixelFormat.TRANSLUCENT);
        pp.gravity = Gravity.CENTER;
        try { wm.addView(panel, pp); expanded = true; }
        catch (Exception e) { panel = null; Toast.makeText(this, "无法展开浮窗", Toast.LENGTH_SHORT).show(); }
    }

    private void closePanel() {
        if (panel != null) {
            try { wm.removeView(panel); } catch (Exception ignored) {}
            panel = null;
        }
        expanded = false;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        closePanel();
        if (bubble != null) { try { wm.removeView(bubble); } catch (Exception ignored) {} }
        stopForeground(true);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }
}
