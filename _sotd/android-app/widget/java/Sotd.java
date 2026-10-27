package io.github.cooperking.sotd;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * What the widgets share: the site's addresses (the calendar plays what its address says: ?do=today, shuffle, mine;
 * ?do=all#day plays all from that day), its songs/today.json (the latest song) and songs/days.json (every day out on
 * the calendar, and the months' colours), the covers (small copies kept on the phone), and opening the app at an address.
 */
final class Sotd {
    static final String SITE = "https://cooperking.github.io/songs/";
    static final String TODAY = SITE + "today.json";
    static final String DAYS = SITE + "days.json";
    // the calendar's month colours (ground, glow), for when days.json hasn't come yet
    static final String[][] MONTHS = {{"#0c1445", "#0094de"}, {"#652bee", "#3333ff"}, {"#11970c", "#429e00"}, {"#b613ec", "#fc00e7"},
        {"#096a42", "#00a336"}, {"#ec1a13", "#fa5300"}, {"#1346ec", "#008ff5"}, {"#d85a06", "#c47c00"}, {"#0b86d3", "#0098b0"},
        {"#ec135b", "#ff3344"}, {"#09696d", "#009e77"}, {"#ec13c1", "#ff3399"}};

    private Sotd() {}

    static String act(String a) { return SITE + "?do=" + a; }
    static String playFrom(String slug) { return SITE + "?do=all#" + slug; }

    static SharedPreferences keep(Context c) { return c.getSharedPreferences("widget", Context.MODE_PRIVATE); }

    static byte[] get(String address) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(address).openConnection();
        c.setConnectTimeout(8000);
        c.setReadTimeout(8000);
        c.setUseCaches(false);
        c.setRequestProperty("User-Agent", "SongOfTheDayWidget/1.0");
        try {
            if (c.getResponseCode() != 200) throw new Exception("HTTP " + c.getResponseCode());
            InputStream in = c.getInputStream();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] b = new byte[16384];
            int n;
            while ((n = in.read(b)) > 0) out.write(b, 0, n);
            in.close();
            return out.toByteArray();
        } finally {
            c.disconnect();
        }
    }

    /** The latest song (today.json): fresh, or the last one had when the site can't be reached; null if never had. */
    static JSONObject today(Context context) {
        SharedPreferences keep = keep(context);
        String json = keep.getString("feed", null);
        try {   // (a new address each ten minutes, past any cache)
            String fresh = new String(get(TODAY + "?t=" + (System.currentTimeMillis() / 600000L)), "UTF-8");
            new JSONObject(fresh);
            json = fresh;
            keep.edit().putString("feed", json).apply();
        } catch (Throwable t) {
            // offline, or the site busy: the last one
        }
        try {
            return json == null ? null : new JSONObject(json);
        } catch (Throwable t) {
            return null;
        }
    }

    /** Every day (days.json): looked for again at most every 20 minutes; the last list had otherwise. */
    static JSONObject days(Context context) {
        SharedPreferences keep = keep(context);
        String json = keep.getString("days", null);
        if (json == null || System.currentTimeMillis() - keep.getLong("daysAt", 0) > 20 * 60000L) {
            try {
                String fresh = new String(get(DAYS + "?t=" + (System.currentTimeMillis() / 600000L)), "UTF-8");
                new JSONObject(fresh).getJSONArray("days");
                json = fresh;
                keep.edit().putString("days", json).putLong("daysAt", System.currentTimeMillis()).apply();
            } catch (Throwable t) {
                // the last list
            }
        }
        try {
            return json == null ? null : new JSONObject(json);
        } catch (Throwable t) {
            return null;
        }
    }

    /** The month's ground and glow (month 1 to 12), from days.json's palettes when there. */
    static int[] palette(JSONObject days, int month) {
        int i = Math.max(0, Math.min(11, month - 1));
        String g = MONTHS[i][0], w = MONTHS[i][1];
        try {
            JSONObject p = days.getJSONArray("palettes").getJSONObject(i);
            g = p.getString("ground");
            w = p.getJSONArray("glow").getString(0);
        } catch (Throwable t) {
            // the colours above
        }
        return new int[]{parse(g, 0xFFEC135B), parse(w, 0xFFFF3344)};
    }

    static int parse(String c, int or) {
        try {
            return Color.parseColor(c);
        } catch (Throwable t) {
            return or;
        }
    }

    private static File coverFile(Context c, String url) {
        File d = new File(c.getCacheDir(), "covers");
        d.mkdirs();
        String name = url.substring(url.lastIndexOf('/') + 1);
        return new File(d, name.isEmpty() ? "x" : name + ".jpg");
    }

    /** A cover kept on the phone, px pixels square (small and plain, so many fit in one update); null if not kept yet. */
    static Bitmap cover(Context c, String url, int px) {
        if (url == null || url.isEmpty()) return null;
        File f = coverFile(c, url);
        if (!f.exists()) return null;
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(f.getPath(), o);
        int s = 1;
        while (o.outWidth / (s * 2) >= px) s *= 2;
        o = new BitmapFactory.Options();
        o.inSampleSize = s;
        o.inPreferredConfig = Bitmap.Config.RGB_565;
        Bitmap b = BitmapFactory.decodeFile(f.getPath(), o);
        if (b == null) return null;
        Bitmap out = Bitmap.createScaledBitmap(b, px, px, true);
        if (out != b) b.recycle();
        return out.getConfig() == Bitmap.Config.RGB_565 ? out : out.copy(Bitmap.Config.RGB_565, false);
    }

    /** The covers not kept yet, fetched six at a time (ten seconds at most). */
    static void fetchCovers(final Context c, List<String> urls) {
        List<String> need = new ArrayList<>();
        for (String u : urls) if (u != null && !u.isEmpty() && !coverFile(c, u).exists() && !need.contains(u)) need.add(u);
        if (need.isEmpty()) return;
        ExecutorService pool = Executors.newFixedThreadPool(6);
        for (final String u : need) {
            pool.execute(new Runnable() {
                @Override
                public void run() {
                    try {
                        byte[] img = get(u);
                        if (BitmapFactory.decodeByteArray(img, 0, img.length) == null) return;
                        File f = coverFile(c, u), tmp = new File(f.getPath() + ".part");
                        FileOutputStream out = new FileOutputStream(tmp);
                        out.write(img);
                        out.close();
                        tmp.renameTo(f);
                    } catch (Throwable t) {
                        // next time
                    }
                }
            });
        }
        pool.shutdown();
        try {
            pool.awaitTermination(10, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            // what's in by now
        }
    }

    /** Opening the app at a calendar address (the app's own copy of the site). */
    static PendingIntent open(Context context, String url, int code) {
        if (url == null || !url.startsWith(SITE)) url = SITE;
        Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url)).setPackage(context.getPackageName()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return PendingIntent.getActivity(context, code, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    /** What a tap on Song of the Day plays: today (all from the latest song), shuffle, mine, day (that song only). */
    static String tapUrl(String tap, String dayUrl) {
        if ("shuffle".equals(tap)) return act("shuffle");
        if ("mine".equals(tap)) return act("mine");
        if ("day".equals(tap)) return dayUrl != null && dayUrl.startsWith(SITE) ? dayUrl : SITE;
        return act("today");
    }

    static JSONArray dayList(JSONObject days) {
        try {
            return days.getJSONArray("days");
        } catch (Throwable t) {
            return new JSONArray();
        }
    }
}
