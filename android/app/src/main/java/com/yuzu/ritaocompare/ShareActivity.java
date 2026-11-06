package com.yuzu.ritaocompare;

import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Toast;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Handles "share to 日淘比价助手" from the Ritao product page. No permission needed. */
public class ShareActivity extends Activity {

    private static final Pattern URL_RE = Pattern.compile("https?://[^\\s\"'<>]+");

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        String text = getIntent().getStringExtra(Intent.EXTRA_TEXT);
        if (text == null || text.trim().isEmpty()) text = getIntent().getStringExtra(Intent.EXTRA_SUBJECT);
        if (text == null) text = "";
        // Many shopping apps share an image with the caption in clipData, or
        // send SEND_MULTIPLE. Look there too.
        ClipData cd = getIntent().getClipData();
        if (text.trim().isEmpty() && cd != null) {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < cd.getItemCount(); i++) {
                ClipData.Item item = cd.getItemAt(i);
                CharSequence cs = item.getText();
                if (cs != null && cs.length() > 0) sb.append(cs.toString()).append(' ');
                else if (item.getUri() != null) sb.append(item.getUri().toString()).append(' ');
            }
            text = sb.toString();
        }
        if (text.trim().isEmpty()) {
            Toast.makeText(this, "没读到商品名，请在比价面板里粘贴；或在日淘市集用「复制商品名」", Toast.LENGTH_LONG).show();
        }

        String url = "";
        Matcher m = URL_RE.matcher(text);
        if (m.find()) url = m.group();

        String kw = clean(text);
        CaptureService.lastKeyword = kw;
        CaptureService.lastSourceUrl = url;

        try {
            Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(Prefs.panelUrl(this, kw, url)));
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (Exception e) {
            Toast.makeText(this, "打不开浏览器，请检查服务地址", Toast.LENGTH_LONG).show();
        }
        finish();
    }

    private String clean(String s) {
        String t = URL_RE.matcher(s).replaceAll(" ");
        t = t.replaceAll("[#【】\\[\\]（）()|｜/／,，。.、!！?？~～]+", " ");
        t = t.replaceAll("(日淘市集|分享|复制|链接|来自|商品|包邮|现货|代购)\\s*", " ");
        t = t.replaceAll("\\s+", " ").trim();
        return t.length() > 120 ? t.substring(0, 120) : t;
    }
}
