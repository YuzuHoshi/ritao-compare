package com.yuzu.ritaocompare;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity {

    private static final Pattern URL_RE = Pattern.compile("https?://[^\\s\"'<>]+");

    private EditText hostEdit;
    private TextView status;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        CrashLog.install(this);
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.parseColor("#0F1217"));
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(36, 52, 36, 40);
        scroll.addView(root);
        setContentView(scroll);

        root.addView(label("日淘比价助手", 20, "#E8ECF2", 0));
        root.addView(label("在日淘市集商品页一键比价 · 闲鱼 / 煤炉 / 雅虎日拍 / 骏河屋 / 乐天 / 日亚", 12, "#8B95A5", 18));

        root.addView(label("电脑上的服务地址", 13, "#E9A23B", 28));
        hostEdit = new EditText(this);
        hostEdit.setText(Prefs.host(this));
        hostEdit.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        hostEdit.setTextColor(Color.parseColor("#E8ECF2"));
        hostEdit.setHintTextColor(Color.parseColor("#616C7D"));
        hostEdit.setBackgroundColor(Color.parseColor("#1D222B"));
        hostEdit.setPadding(20, 16, 20, 16);
        root.addView(hostEdit);
        root.addView(button("保存地址", v -> {
            Prefs.setHost(this, hostEdit.getText().toString());
            Toast.makeText(this, "已保存：" + Prefs.host(this), Toast.LENGTH_SHORT).show();
            refresh();
        }));
        root.addView(label("手机和电脑必须连同一个 Wi-Fi。默认是占位地址，请改成电脑浏览器地址栏里的局域网 IP（把 127.0.0.1 换成 192.168.x.x），端口保持 8787，再点「保存地址」。", 11, "#616C7D", 8));

        status = label("", 12, "#8B95A5", 24);
        root.addView(status);

        String crash = CrashLog.read(this);
        if (!crash.isEmpty()) {
            final String crashText = crash;
            root.addView(label("上次闪退记录（复制发给开发者可快速定位）", 12, "#E3695E", 18));
            root.addView(button("复制崩溃日志", v -> {
                try {
                    ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                    if (cm != null) cm.setPrimaryClip(ClipData.newPlainText("crash", crashText));
                    Toast.makeText(this, "已复制，可粘贴发给我", Toast.LENGTH_SHORT).show();
                    CrashLog.clear(this);
                } catch (Throwable ignored) {}
            }));
            root.addView(label(crash.length() > 1400 ? crash.substring(0, 1400) : crash, 10, "#8B95A5", 6));
        }

        root.addView(label("① 最可靠：复制商品名（不需要任何权限）", 13, "#E9A23B", 20));
        root.addView(button("从剪贴板读取并查价", v -> {
            String text = readClipboard();
            if (text.isEmpty()) {
                Toast.makeText(this, "剪贴板是空的。先去日淘市集商品页点「复制商品名 / 复制链接」", Toast.LENGTH_LONG).show();
                return;
            }
            Matcher m = URL_RE.matcher(text);
            String url = m.find() ? m.group() : "";
            CaptureService.lastKeyword = text;
            CaptureService.lastSourceUrl = url;
            try {
                Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(Prefs.panelUrl(this, text, url)));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(i);
            } catch (Exception e) {
                Toast.makeText(this, "打不开浏览器，检查服务地址", Toast.LENGTH_LONG).show();
            }
        }));
        root.addView(label("在日淘市集商品页点「复制商品名 / 复制链接」→ 回到本页点上面的按钮，直接出比价面板。这条不依赖系统分享，最稳。", 12, "#8B95A5", 8));

        root.addView(label("② 系统分享（部分 App 看不到）", 13, "#E9A23B", 24));
        root.addView(label("若日淘市集的分享菜单里有「日淘比价助手」，点它即可。若没有，说明它用的是自家分享面板（只列微信/QQ/复制链接），第三方 App 收不到 —— 用 ① 就行。", 12, "#8B95A5", 8));

        root.addView(label("③ 悬浮球（需要两个权限）", 13, "#E9A23B", 24));
        root.addView(button("授予悬浮窗权限", v -> {
            Intent i = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName()));
            startActivity(i);
        }));
        root.addView(button("开启无障碍读商品名", v -> {
            startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
            Toast.makeText(this, "在列表里找到「日淘比价助手」并打开", Toast.LENGTH_LONG).show();
        }));
        root.addView(button("启动悬浮球", v -> {
            if (!Settings.canDrawOverlays(this)) {
                Toast.makeText(this, "请先授予悬浮窗权限", Toast.LENGTH_LONG).show();
                return;
            }
            try {
                startService(new Intent(this, FloatingService.class));
                Toast.makeText(this, "悬浮球已启动，回到日淘市集就能看到", Toast.LENGTH_SHORT).show();
            } catch (Throwable t) {
                Toast.makeText(this, "启动失败：" + t.getMessage(), Toast.LENGTH_LONG).show();
            }
            refresh();
        }));
        root.addView(button("停止悬浮球", v -> { stopService(new Intent(this, FloatingService.class)); refresh(); }));

        root.addView(button("用当前读到的商品名打开面板", v -> {
            String kw = CaptureService.lastKeyword;
            if (kw == null || kw.trim().isEmpty()) {
                Toast.makeText(this, "还没读到商品名，先去商品页或用分享", Toast.LENGTH_SHORT).show();
                return;
            }
            try {
                Intent i = new Intent(Intent.ACTION_VIEW,
                        Uri.parse(Prefs.panelUrl(this, kw, CaptureService.lastSourceUrl)));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(i);
            } catch (Exception e) {
                Toast.makeText(this, "打开失败，检查服务地址", Toast.LENGTH_LONG).show();
            }
        }));

        root.addView(label("只读取你在看的商品名，全部留在本机；不上传、不批量抓取、不代发消息。", 11, "#616C7D", 28));
        refresh();
    }

    @Override
    protected void onResume() { super.onResume(); refresh(); }

    private String readClipboard() {
        // Works reliably because this Activity is in the foreground when called.
        try {
            ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            if (cm == null || !cm.hasPrimaryClip()) return "";
            ClipData clip = cm.getPrimaryClip();
            if (clip == null || clip.getItemCount() == 0) return "";
            ClipData.Item item = clip.getItemAt(0);
            CharSequence cs = item.getText();
            if (cs != null) return cs.toString().trim();
            return item.getUri() == null ? "" : item.getUri().toString();
        } catch (Throwable t) {
            return "";
        }
    }

    private void refresh() {
        boolean overlay = Settings.canDrawOverlays(this);
        String a11y = Settings.Secure.getString(getContentResolver(),
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        boolean acc = a11y != null && a11y.contains(getPackageName());
        status.setText("悬浮窗权限：" + (overlay ? "已开启" : "未开启")
                + "   无障碍：" + (acc ? "已开启" : "未开启")
                + "\n服务：" + Prefs.host(this)
                + "\n最近读到：" + (CaptureService.lastKeyword.isEmpty() ? "（无）" : CaptureService.lastKeyword));
    }

    private TextView label(String text, int size, String color, int topMargin) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextSize(size);
        t.setTextColor(Color.parseColor(color));
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        p.topMargin = topMargin;
        t.setLayoutParams(p);
        return t;
    }

    private Button button(String text, View.OnClickListener listener) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextColor(Color.parseColor("#E8ECF2"));
        b.setTextSize(13f);
        b.setGravity(Gravity.CENTER_VERTICAL);
        GradientDrawable gd = new GradientDrawable();
        gd.setColor(Color.parseColor("#1D222B"));
        gd.setCornerRadius(18f);
        gd.setStroke(2, Color.parseColor("#2A313C"));
        b.setBackground(gd);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        p.topMargin = 12;
        b.setLayoutParams(p);
        b.setOnClickListener(listener);
        return b;
    }
}
