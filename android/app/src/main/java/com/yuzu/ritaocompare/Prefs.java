package com.yuzu.ritaocompare;

import android.content.Context;
import android.content.SharedPreferences;

import java.net.URLEncoder;

public final class Prefs {
    private static final String NAME = "ritao_compare";
    // Generic loopback placeholder. The user must replace it in the app with
    // the PC's LAN address (see MainActivity). Never hardcode a private LAN IP.
    public static final String DEFAULT_HOST = "http://127.0.0.1:8787";

    private Prefs() {}

    private static SharedPreferences sp(Context c) {
        return c.getSharedPreferences(NAME, Context.MODE_PRIVATE);
    }

    public static String host(Context c) {
        String h = sp(c).getString("host", DEFAULT_HOST).trim();
        if (h.isEmpty()) h = DEFAULT_HOST;
        if (!h.startsWith("http://") && !h.startsWith("https://")) h = "http://" + h;
        return h.replaceAll("/+$", "");
    }

    public static void setHost(Context c, String h) {
        sp(c).edit().putString("host", h == null ? "" : h.trim()).apply();
    }

    public static String panelUrl(Context c, String keyword, String sourceUrl) {
        try {
            String u = host(c) + "/?keyword=" + URLEncoder.encode(keyword == null ? "" : keyword, "UTF-8");
            if (sourceUrl != null && !sourceUrl.isEmpty()) {
                u += "&source_url=" + URLEncoder.encode(sourceUrl, "UTF-8");
            }
            return u;
        } catch (Exception e) {
            return host(c) + "/";
        }
    }
}
