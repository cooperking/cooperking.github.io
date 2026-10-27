package io.github.cooperking.sotd;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.RemoteViews;
import android.widget.TextView;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Only in the check's build (make.js with WIDGET_PREVIEW=1): the widgets drawn on a page, at a phone's widget sizes,
 * for pictures of them (check: widget-shots.sh). Page 1: Song of the Day, Play and the last seven days; page 2 the
 * month. Its days list from the address given (extra "days"), if any.
 */
public class WidgetPreview extends Activity {
    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        final int page = getIntent().getIntExtra("page", 1);
        final String daysUrl = getIntent().getStringExtra("days");
        final LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setBackgroundColor(Color.parseColor("#2b2d33"));
        box.setPadding(dp(16), dp(40), dp(16), dp(16));
        setContentView(box);
        new Thread(() -> {
            try {
                if (daysUrl != null) Sotd.keep(this).edit().putString("days", new String(Sotd.get(daysUrl), "UTF-8")).putLong("daysAt", System.currentTimeMillis()).apply();
            } catch (Throwable t) { /* the site's */ }
            final JSONObject today = Sotd.today(this);
            final JSONObject days = Sotd.days(this);
            String cu = today == null ? "" : today.optString("cover", "");
            Sotd.fetchCovers(this, Collections.singletonList(cu));
            List<String> need = new ArrayList<>(CalendarWidget.covers(days, 0));
            Sotd.fetchCovers(this, need);
            runOnUiThread(() -> {
                if (page == 1) {
                    add(box, "Song of the Day", TodayWidget.views(this, today, Sotd.cover(this, cu, 240), "today", 1), 150);
                    add(box, "Play", PlayWidget.views(this, today, Sotd.cover(this, cu, 200), 2), 150);
                    add(box, "Calendar, short", CalendarWidget.weekViews(this, days, 3), 150);
                } else {
                    add(box, "Calendar", CalendarWidget.monthViews(this, days, 0, 4), 340);
                }
            });
        }).start();
    }

    void add(LinearLayout box, String name, RemoteViews v, int heightDp) {
        TextView t = new TextView(this);
        t.setText(name);
        t.setTextColor(Color.WHITE);
        t.setPadding(0, dp(10), 0, dp(6));
        box.addView(t);
        FrameLayout f = new FrameLayout(this);
        box.addView(f, new LinearLayout.LayoutParams(dp(340), dp(heightDp)));
        f.addView(v.apply(this, f));
    }

    int dp(int x) { return Math.round(x * getResources().getDisplayMetrics().density); }
}
