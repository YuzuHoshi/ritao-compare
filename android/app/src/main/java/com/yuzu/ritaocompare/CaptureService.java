package com.yuzu.ritaocompare;

import android.accessibilityservice.AccessibilityService;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Reads the product name off the Ritao (Flutter) product page.
 * Flutter exposes widget text through the Android semantics tree, so this works
 * without OCR. If the tree is empty the share-target path still works.
 * Nothing leaves the device -- the string is only kept in memory.
 */
public class CaptureService extends AccessibilityService {

    public static volatile String lastKeyword = "";
    public static volatile String lastSourceUrl = "";
    public static volatile long lastUpdate = 0L;

    private static final String TARGET_PKG = "com.wonderfull.ritao";
    private static final Pattern URL_RE = Pattern.compile("https?://[^\\s\"'<>]+");
    private static final Pattern BAD = Pattern.compile(
            "购物车|首页|站点|我的|搜索|立即|加入|收藏|客服|登录|注册|运费|结算|确认|返回|分享|翻译|加载|日淘市集|帮助|更多|筛选|排序|评价|详情|包邮|商品编号|已售|库存|选择|规格|数量|合计|优惠|店铺|公告|通知|设置|分享给|复制链接");
    private static final Pattern CJK = Pattern.compile("[\\u3040-\\u30ff\\u3400-\\u9fff]");

    private long lastScan = 0L;

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null || event.getPackageName() == null) return;
        if (!TARGET_PKG.contentEquals(event.getPackageName())) return;
        long now = System.currentTimeMillis();
        if (now - lastScan < 700) return;
        lastScan = now;

        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return;
        List<String> texts = new ArrayList<>();
        try {
            collect(root, texts, 0);
        } catch (Exception ignored) {
            // A detached node can throw; the next event will try again.
        }
        root.recycle();

        String best = "";
        String foundUrl = "";
        for (String t : texts) {
            if (foundUrl.isEmpty()) {
                Matcher m = URL_RE.matcher(t);
                if (m.find()) foundUrl = m.group();
            }
            if (URL_RE.matcher(t).find()) continue;
            if (BAD.matcher(t).find()) continue;
            if (t.length() < 8 || t.length() > 120) continue;
            Matcher cm = CJK.matcher(t);
            int cjk = 0;
            while (cm.find()) cjk++;
            if (cjk < 2 && t.length() < 14) continue;
            if (t.length() > best.length()) best = t;
        }
        if (!best.isEmpty()) {
            lastKeyword = best;
            lastUpdate = now;
        }
        if (!foundUrl.isEmpty()) lastSourceUrl = foundUrl;
    }

    private void collect(AccessibilityNodeInfo node, List<String> out, int depth) {
        if (node == null || depth > 24 || out.size() > 400) return;
        CharSequence cs = node.getText();
        if (cs != null) add(out, cs);
        CharSequence cd = node.getContentDescription();
        if (cd != null) add(out, cd);
        int n = node.getChildCount();
        for (int i = 0; i < n; i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                collect(child, out, depth + 1);
                child.recycle();
            }
        }
    }

    private void add(List<String> out, CharSequence cs) {
        String t = cs.toString().trim().replaceAll("\\s+", " ");
        if (t.length() >= 4 && t.length() <= 200 && !out.contains(t)) out.add(t);
    }

    @Override
    public void onInterrupt() {}
}
