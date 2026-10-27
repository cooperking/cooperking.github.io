package io.github.cooperking.sotd;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.net.Uri;
import android.util.Base64;
import android.widget.RemoteViews;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * The Home Screen widget: the latest Song of the Day (the site's songs/today.json, written with the share pages), its
 * cover, title, artist and day, on that month's colour. Tapping it opens the app at the day. Android asks it to look
 * again every half hour; it keeps the last one it had when the site can't be reached.
 */
public class TodayWidget extends AppWidgetProvider {
    static final String FEED = "https://cooperking.github.io/songs/today.json";
    static final String SITE = "https://cooperking.github.io/songs/";

    @Override
    public void onUpdate(Context context, final AppWidgetManager manager, final int[] ids) {
        final PendingResult done = goAsync();
        final Context app = context.getApplicationContext();
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    refresh(app, manager, ids);
                } catch (Throwable t) {
                    // (nothing to show it to: the widget keeps what it had)
                } finally {
                    done.finish();
                }
            }
        }).start();
    }

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

    static void refresh(Context context, AppWidgetManager manager, int[] ids) {
        SharedPreferences keep = context.getSharedPreferences("widget", Context.MODE_PRIVATE);
        String json = keep.getString("feed", null);
        try {   // (a new address each ten minutes, past any cache)
            String fresh = new String(get(FEED + "?t=" + (System.currentTimeMillis() / 600000L)), "UTF-8");
            new JSONObject(fresh);
            json = fresh;
            keep.edit().putString("feed", json).apply();
        } catch (Throwable t) {
            // offline, or the site busy: the last one
        }
        JSONObject j = null;
        try {
            if (json != null) j = new JSONObject(json);
        } catch (Throwable t) {
            j = null;
        }
        Bitmap cover = null;
        if (j != null) {
            String cu = j.optString("cover", "");
            if (cu.length() > 0) {
                if (!cu.equals(keep.getString("coverUrl", ""))) {
                    try {
                        byte[] img = get(cu);
                        keep.edit().putString("cover", Base64.encodeToString(img, Base64.NO_WRAP)).putString("coverUrl", cu).apply();
                    } catch (Throwable t) {
                        // the cover stays empty until it can be fetched
                    }
                }
                String b64 = cu.equals(keep.getString("coverUrl", "")) ? keep.getString("cover", null) : null;
                if (b64 != null) {
                    byte[] img = Base64.decode(b64, Base64.NO_WRAP);
                    cover = BitmapFactory.decodeByteArray(img, 0, img.length);
                    if (cover != null && cover.getWidth() > 300) cover = Bitmap.createScaledBitmap(cover, 300, 300, true);
                }
            }
        }
        for (int id : ids) manager.updateAppWidget(id, views(context, j, cover));
    }

    static RemoteViews views(Context context, JSONObject j, Bitmap cover) {
        RemoteViews v = new RemoteViews(context.getPackageName(), R.layout.widget_today);
        String url = SITE;
        int ground = 0xFFEC135B;
        if (j != null) {
            v.setTextViewText(R.id.w_title, j.optString("title", "Song of the Day"));
            v.setTextViewText(R.id.w_artist, j.optString("artist", "").toUpperCase());
            v.setTextViewText(R.id.w_day, j.optString("day", "").toUpperCase());
            url = j.optString("url", SITE);
            try {
                ground = Color.parseColor(j.optString("ground", "#ec135b"));
            } catch (Throwable t) {
                ground = 0xFFEC135B;
            }
        } else {
            v.setTextViewText(R.id.w_title, "Song of the Day");
            v.setTextViewText(R.id.w_artist, "JOHN’S SONG OF THE DAY");
            v.setTextViewText(R.id.w_day, "");
        }
        v.setInt(R.id.w_bg, "setColorFilter", ground);
        if (cover != null) v.setImageViewBitmap(R.id.w_cover, cover);
        else v.setImageViewResource(R.id.w_cover, R.drawable.widget_cover_empty);
        if (!url.startsWith(SITE)) url = SITE;
        Intent open = new Intent(Intent.ACTION_VIEW, Uri.parse(url)).setPackage(context.getPackageName()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        v.setOnClickPendingIntent(R.id.w_root, PendingIntent.getActivity(context, 0, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        return v;
    }
}
