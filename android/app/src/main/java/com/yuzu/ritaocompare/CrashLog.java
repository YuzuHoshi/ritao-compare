package com.yuzu.ritaocompare;

import android.content.Context;
import android.os.Build;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/** Writes the last crash stack trace locally so it can be copied out of the UI. */
public final class CrashLog {

    private static final String FILE = "last_crash.txt";

    private CrashLog() {}

    public static void install(final Context ctx) {
        final Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, error) -> {
            try {
                StringWriter sw = new StringWriter();
                error.printStackTrace(new PrintWriter(sw));
                String body = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(new Date())
                        + "\n" + Build.MANUFACTURER + " " + Build.MODEL
                        + " / Android " + Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + ")\n\n"
                        + sw;
                try (FileOutputStream out = ctx.openFileOutput(FILE, Context.MODE_PRIVATE)) {
                    out.write(body.getBytes("UTF-8"));
                }
            } catch (Throwable ignored) {
                // Never throw from the crash handler.
            }
            if (previous != null) previous.uncaughtException(thread, error);
        });
    }

    public static String read(Context ctx) {
        try {
            File f = new File(ctx.getFilesDir(), FILE);
            if (!f.exists()) return "";
            byte[] b = new byte[(int) f.length()];
            try (FileInputStream in = new FileInputStream(f)) {
                int n, off = 0;
                while (off < b.length && (n = in.read(b, off, b.length - off)) > 0) off += n;
            }
            return new String(b, "UTF-8");
        } catch (Throwable ignored) {
            return "";
        }
    }

    public static void clear(Context ctx) {
        try { new File(ctx.getFilesDir(), FILE).delete(); } catch (Throwable ignored) {}
    }
}
