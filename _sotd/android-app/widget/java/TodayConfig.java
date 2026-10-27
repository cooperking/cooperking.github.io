package io.github.cooperking.sotd;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.content.Intent;
import android.os.Bundle;
import android.util.TypedValue;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.TextView;

/**
 * Song of the Day's settings: what a tap plays. Shown when the widget is added (before Android 12; from 12 it's added
 * as Play all, and holding it offers these settings).
 */
public class TodayConfig extends Activity {
    static final String[][] CHOICES = {{"today", "Play all from the latest song"}, {"shuffle", "Shuffle"}, {"mine", "My playlist"},
        {"day", "Only the latest song"}};

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        final int id = getIntent().getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID);
        setResult(RESULT_CANCELED, new Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id));
        if (id == AppWidgetManager.INVALID_APPWIDGET_ID) { finish(); return; }
        setTitle("Song of the Day");
        int pad = dp(24);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(pad, dp(8), pad, dp(16));
        TextView h = new TextView(this);
        h.setText("A tap plays");
        h.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        h.setPadding(0, 0, 0, dp(8));
        box.addView(h);
        final RadioGroup g = new RadioGroup(this);
        String now = TodayWidget.tap(this, id);
        for (int i = 0; i < CHOICES.length; i++) {
            RadioButton r = new RadioButton(this);
            r.setId(100 + i);
            r.setText(CHOICES[i][1]);
            r.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
            r.setPadding(dp(4), dp(10), 0, dp(10));
            g.addView(r);
            if (CHOICES[i][0].equals(now)) g.check(r.getId());
        }
        box.addView(g);
        Button done = new Button(this);
        done.setText("Done");
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(12);
        lp.gravity = android.view.Gravity.END;
        box.addView(done, lp);
        done.setOnClickListener(v -> {
            int i = Math.max(0, g.getCheckedRadioButtonId() - 100);
            Sotd.keep(this).edit().putString("tap_" + id, CHOICES[Math.min(i, CHOICES.length - 1)][0]).apply();
            final AppWidgetManager m = AppWidgetManager.getInstance(this);
            final android.content.Context app = getApplicationContext();
            new Thread(() -> { try { TodayWidget.refresh(app, m, new int[]{id}); } catch (Throwable t) { /* next update */ } }).start();
            setResult(RESULT_OK, new Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id));
            finish();
        });
        setContentView(box);
    }

    private int dp(int x) { return Math.round(x * getResources().getDisplayMetrics().density); }
}
