package io.github.cooperking.sotd;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.graphics.Bitmap;
import android.widget.RemoteViews;

import org.json.JSONObject;

import java.util.Collections;

/**
 * Song of the Day: the latest song (the site's songs/today.json), its cover, title, artist and day, on that month's
 * colour. A tap plays all from it, on through the days before; its settings (TodayConfig: when it's added, and from
 * Android 12 by holding it) choose Play all, Shuffle, My playlist or only that song. Android asks it to look again
 * every half hour; it keeps the last one it had when the site can't be reached.
 */
public class TodayWidget extends AppWidgetProvider {
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

    @Override
    public void onDeleted(Context context, int[] ids) {
        for (int id : ids) Sotd.keep(context).edit().remove("tap_" + id).apply();
    }

    static String tap(Context context, int id) { return Sotd.keep(context).getString("tap_" + id, "today"); }

    static void refresh(Context context, AppWidgetManager manager, int[] ids) {
        JSONObject j = Sotd.today(context);
        String cu = j == null ? "" : j.optString("cover", "");
        Sotd.fetchCovers(context, Collections.singletonList(cu));
        Bitmap cover = Sotd.cover(context, cu, 240);
        for (int id : ids) manager.updateAppWidget(id, views(context, j, cover, tap(context, id), id));
    }

    static RemoteViews views(Context context, JSONObject j, Bitmap cover, String tap, int id) {
        RemoteViews v = new RemoteViews(context.getPackageName(), R.layout.widget_today);
        String day = Sotd.SITE;
        int ground = 0xFFEC135B;
        if (j != null) {
            v.setTextViewText(R.id.w_title, j.optString("title", "Song of the Day"));
            v.setTextViewText(R.id.w_artist, j.optString("artist", "").toUpperCase());
            v.setTextViewText(R.id.w_day, j.optString("day", "").toUpperCase());
            day = j.optString("url", Sotd.SITE);
            ground = Sotd.parse(j.optString("ground", "#ec135b"), 0xFFEC135B);
        } else {
            v.setTextViewText(R.id.w_title, "Song of the Day");
            v.setTextViewText(R.id.w_artist, "JOHN’S SONG OF THE DAY");
            v.setTextViewText(R.id.w_day, "");
        }
        v.setInt(R.id.w_bg, "setColorFilter", ground);
        if (cover != null) v.setImageViewBitmap(R.id.w_cover, cover);
        else v.setImageViewResource(R.id.w_cover, R.drawable.widget_cover_empty);
        v.setOnClickPendingIntent(R.id.w_root, Sotd.open(context, Sotd.tapUrl(tap, day), 1000 + id));
        return v;
    }
}
