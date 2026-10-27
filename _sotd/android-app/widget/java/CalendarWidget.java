package io.github.cooperking.sotd;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.os.Build;
import android.os.Bundle;
import android.util.SizeF;
import android.view.View;
import android.widget.RemoteViews;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Calendar;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;

/**
 * Calendar: a month of the calendar's covers by day (Monday first), today ringed; a tap on a day plays all from it, on
 * through the days before; arrows for the months before (and back), and This month. Made short (from Android 12), the
 * last seven days instead. From the site's songs/days.json; covers kept on the phone.
 */
public class CalendarWidget extends AppWidgetProvider {
    static final String PREV = "io.github.cooperking.sotd.CALENDAR_PREV", NEXT = "io.github.cooperking.sotd.CALENDAR_NEXT",
        NOW = "io.github.cooperking.sotd.CALENDAR_NOW";
    static final String[] MONTHS = {"January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"};
    static final String[] WEEKDAYS = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"};
    static final TimeZone LONDON = TimeZone.getTimeZone("Europe/London");

    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] ids) { later(context, ids); }

    @Override
    public void onAppWidgetOptionsChanged(Context context, AppWidgetManager manager, int id, Bundle options) { later(context, new int[]{id}); }

    @Override
    public void onReceive(Context context, Intent intent) {
        String a = intent.getAction();
        int id = intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID);
        if ((PREV.equals(a) || NEXT.equals(a) || NOW.equals(a)) && id != AppWidgetManager.INVALID_APPWIDGET_ID) {
            int off = Sotd.keep(context).getInt("cal_" + id, 0);
            off = NOW.equals(a) ? 0 : PREV.equals(a) ? off + 1 : Math.max(0, off - 1);
            Sotd.keep(context).edit().putInt("cal_" + id, off).apply();
            later(context, new int[]{id});
            return;
        }
        super.onReceive(context, intent);
    }

    @Override
    public void onDeleted(Context context, int[] ids) {
        for (int id : ids) Sotd.keep(context).edit().remove("cal_" + id).apply();
    }

    private void later(Context context, final int[] ids) {
        final PendingResult done = goAsync();
        final Context app = context.getApplicationContext();
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    refresh(app, ids);
                } catch (Throwable t) {
                    // the widget keeps what it had
                } finally {
                    done.finish();
                }
            }
        }).start();
    }

    static void refresh(Context context, int[] ids) {
        AppWidgetManager m = AppWidgetManager.getInstance(context);
        JSONObject days = Sotd.days(context);
        for (int id : ids) {
            int off = Sotd.keep(context).getInt("cal_" + id, 0);
            m.updateAppWidget(id, views(context, days, off, id));   // with the covers kept already
            List<String> need = covers(days, off);
            int before = 0;
            for (String u : need) if (Sotd.cover(context, u, 8) != null) before++;
            if (before < need.size()) {
                Sotd.fetchCovers(context, need);
                m.updateAppWidget(id, views(context, days, off, id));   // and again with those just fetched
            }
        }
    }

    static Calendar today() {
        Calendar c = Calendar.getInstance(LONDON);
        c.setFirstDayOfWeek(Calendar.MONDAY);
        return c;
    }

    static String key(int y, int m, int d) { return String.format(java.util.Locale.ROOT, "%04d-%02d-%02d", y, m, d); }

    /** The month `off` back from this one, no earlier than the first with a song: { year, month (1 to 12), off }. */
    static int[] month(JSONArray list, int off) {
        Calendar now = today();
        int y = now.get(Calendar.YEAR), m = now.get(Calendar.MONTH) + 1;
        int maxBack = 0;
        if (list.length() > 0) {
            String first = list.optJSONObject(0).optString("date", key(y, m, 1));
            int fy = Integer.parseInt(first.substring(0, 4)), fm = Integer.parseInt(first.substring(5, 7));
            maxBack = Math.max(0, (y - fy) * 12 + (m - fm));
        }
        off = Math.max(0, Math.min(off, maxBack));
        int t = y * 12 + (m - 1) - off;
        return new int[]{t / 12, t % 12 + 1, off, maxBack};
    }

    static List<String> covers(JSONObject days, int off) {
        JSONArray list = Sotd.dayList(days);
        int[] ym = month(list, off);
        String p = String.format(java.util.Locale.ROOT, "%04d-%02d-", ym[0], ym[1]);
        List<String> out = new ArrayList<>();
        for (int i = 0; i < list.length(); i++) {
            JSONObject d = list.optJSONObject(i);
            if (d.optString("date").startsWith(p) || i >= list.length() - 7) out.add(d.optString("cover"));
        }
        return out;
    }

    static RemoteViews views(Context context, JSONObject days, int off, int id) {
        RemoteViews month = monthViews(context, days, off, id);
        if (Build.VERSION.SDK_INT >= 31) {   // short: the last seven days
            Map<SizeF, RemoteViews> sizes = new HashMap<>();
            sizes.put(new SizeF(200f, 100f), weekViews(context, days, id));
            sizes.put(new SizeF(200f, 230f), month);
            return new RemoteViews(sizes);
        }
        return month;
    }

    static RemoteViews monthViews(Context context, JSONObject days, int offset, int id) {
        String pkg = context.getPackageName();
        RemoteViews v = new RemoteViews(pkg, R.layout.widget_calendar);
        JSONArray list = Sotd.dayList(days);
        int[] ym = month(list, offset);
        int y = ym[0], m = ym[1], off = ym[2], maxBack = ym[3];
        int[] pal = Sotd.palette(days, m);
        v.setInt(R.id.k_bg, "setColorFilter", pal[0]);
        v.setTextViewText(R.id.k_month, MONTHS[m - 1] + " " + y);
        Map<String, JSONObject> byDate = new HashMap<>();
        for (int i = 0; i < list.length(); i++) { JSONObject d = list.optJSONObject(i); byDate.put(d.optString("date"), d); }
        Calendar c = Calendar.getInstance(LONDON);
        c.clear();
        c.set(y, m - 1, 1);
        int lead = (c.get(Calendar.DAY_OF_WEEK) + 5) % 7, count = c.getActualMaximum(Calendar.DAY_OF_MONTH);
        Calendar now = today();
        String todayKey = key(now.get(Calendar.YEAR), now.get(Calendar.MONTH) + 1, now.get(Calendar.DAY_OF_MONTH));
        int rows = (lead + count + 6) / 7;
        for (int r = 0; r < 6; r++) v.setViewVisibility(id(context, "r" + r), r < rows ? View.VISIBLE : View.GONE);
        for (int i = 0; i < 42; i++) {
            int cell = id(context, "c" + i), img = id(context, "ci" + i), num = id(context, "ct" + i);
            int n = i - lead + 1;
            if (n < 1 || n > count) { v.setViewVisibility(cell, View.INVISIBLE); continue; }
            v.setViewVisibility(cell, View.VISIBLE);
            String k = key(y, m, n);
            JSONObject d = byDate.get(k);
            v.setTextViewText(num, String.valueOf(n));
            v.setInt(cell, "setBackgroundResource", k.equals(todayKey) ? R.drawable.widget_cell_today : R.drawable.widget_cell);
            if (d != null) {
                Bitmap b = Sotd.cover(context, d.optString("cover"), 80);
                if (b != null) v.setImageViewBitmap(img, b); else v.setImageViewResource(img, R.drawable.widget_cell);
                v.setViewVisibility(img, View.VISIBLE);
                v.setOnClickPendingIntent(cell, Sotd.open(context, Sotd.playFrom(d.optString("slug")), 3000 + id * 64 + i));
            } else {
                v.setViewVisibility(img, View.INVISIBLE);
                v.setOnClickPendingIntent(cell, Sotd.open(context, Sotd.SITE, 3000 + id * 64 + i));
            }
        }
        v.setViewVisibility(R.id.k_now, off > 0 ? View.VISIBLE : View.GONE);
        v.setInt(R.id.k_prev, "setImageAlpha", off < maxBack ? 255 : 90);
        v.setInt(R.id.k_next, "setImageAlpha", off > 0 ? 255 : 90);
        v.setOnClickPendingIntent(R.id.k_prev, nav(context, PREV, id, 1));
        v.setOnClickPendingIntent(R.id.k_next, nav(context, NEXT, id, 2));
        v.setOnClickPendingIntent(R.id.k_now, nav(context, NOW, id, 3));
        v.setOnClickPendingIntent(R.id.k_root, Sotd.open(context, Sotd.SITE, 3000 + id * 64 + 60));
        return v;
    }

    static RemoteViews weekViews(Context context, JSONObject days, int id) {
        RemoteViews v = new RemoteViews(context.getPackageName(), R.layout.widget_week);
        JSONArray list = Sotd.dayList(days);
        Calendar now = today();
        int m = now.get(Calendar.MONTH) + 1;
        v.setInt(R.id.x_bg, "setColorFilter", Sotd.palette(days, m)[0]);
        v.setTextViewText(R.id.x_month, MONTHS[m - 1] + " " + now.get(Calendar.YEAR));
        String todayKey = key(now.get(Calendar.YEAR), m, now.get(Calendar.DAY_OF_MONTH));
        int start = Math.max(0, list.length() - 7);
        for (int i = 0; i < 7; i++) {
            int box = id(context, "w" + i), img = id(context, "wi" + i), wd = id(context, "ww" + i), dn = id(context, "wd" + i);
            JSONObject d = start + i < list.length() ? list.optJSONObject(start + i) : null;
            if (d == null) { v.setViewVisibility(box, View.INVISIBLE); continue; }
            String date = d.optString("date");
            Calendar c = Calendar.getInstance(LONDON);
            c.clear();
            c.set(Integer.parseInt(date.substring(0, 4)), Integer.parseInt(date.substring(5, 7)) - 1, Integer.parseInt(date.substring(8, 10)));
            v.setTextViewText(wd, WEEKDAYS[c.get(Calendar.DAY_OF_WEEK) - 1]);
            v.setTextViewText(dn, String.valueOf(c.get(Calendar.DAY_OF_MONTH)));
            Bitmap b = Sotd.cover(context, d.optString("cover"), 96);
            if (b != null) v.setImageViewBitmap(img, b); else v.setImageViewResource(img, R.drawable.widget_cell);
            v.setInt(img, "setBackgroundResource", date.equals(todayKey) ? R.drawable.widget_cell_today : R.drawable.widget_cell);
            v.setOnClickPendingIntent(box, Sotd.open(context, Sotd.playFrom(d.optString("slug")), 3000 + id * 64 + 50 + i));
        }
        v.setOnClickPendingIntent(R.id.x_root, Sotd.open(context, Sotd.SITE, 3000 + id * 64 + 61));
        return v;
    }

    static PendingIntent nav(Context context, String action, int id, int k) {
        Intent i = new Intent(context, CalendarWidget.class).setAction(action).putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id);
        return PendingIntent.getBroadcast(context, 9000 + id * 4 + k, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    static int id(Context context, String name) { return context.getResources().getIdentifier(name, "id", context.getPackageName()); }
}
