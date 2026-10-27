package io.github.cooperking.sotd;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.graphics.Bitmap;
import android.widget.RemoteViews;

import org.json.JSONObject;

import java.util.Collections;

/** Play: the latest song, with Play all (from it, on through the days before), Shuffle and My playlist. */
public class PlayWidget extends AppWidgetProvider {
    @Override
    public void onUpdate(Context context, final AppWidgetManager manager, final int[] ids) {
        final PendingResult done = goAsync();
        final Context app = context.getApplicationContext();
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    JSONObject j = Sotd.today(app);
                    String cu = j == null ? "" : j.optString("cover", "");
                    Sotd.fetchCovers(app, Collections.singletonList(cu));
                    Bitmap cover = Sotd.cover(app, cu, 200);
                    for (int id : ids) manager.updateAppWidget(id, views(app, j, cover, id));
                } catch (Throwable t) {
                    // the widget keeps what it had
                } finally {
                    done.finish();
                }
            }
        }).start();
    }

    static RemoteViews views(Context context, JSONObject j, Bitmap cover, int id) {
        RemoteViews v = new RemoteViews(context.getPackageName(), R.layout.widget_play);
        v.setTextViewText(R.id.p_title, j == null ? "Song of the Day" : j.optString("title", "Song of the Day"));
        v.setTextViewText(R.id.p_artist, j == null ? "" : j.optString("artist", "").toUpperCase());
        v.setTextViewText(R.id.p_day, j == null ? "" : j.optString("day", "").toUpperCase());
        v.setInt(R.id.p_bg, "setColorFilter", j == null ? 0xFFEC135B : Sotd.parse(j.optString("ground", "#ec135b"), 0xFFEC135B));
        if (cover != null) v.setImageViewBitmap(R.id.p_cover, cover);
        else v.setImageViewResource(R.id.p_cover, R.drawable.widget_cover_empty);
        v.setOnClickPendingIntent(R.id.p_root, Sotd.open(context, Sotd.act("today"), 2000 + id * 4));
        v.setOnClickPendingIntent(R.id.p_all, Sotd.open(context, Sotd.act("today"), 2000 + id * 4 + 1));
        v.setOnClickPendingIntent(R.id.p_shuffle, Sotd.open(context, Sotd.act("shuffle"), 2000 + id * 4 + 2));
        v.setOnClickPendingIntent(R.id.p_mine, Sotd.open(context, Sotd.act("mine"), 2000 + id * 4 + 3));
        return v;
    }
}
