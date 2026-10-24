package io.github.cooperking.sotd;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.JavascriptInterface;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * John's Song of the Day on a Fire TV: the site in a full-screen browser view. The page knows it's on a TV from
 * the browser's name (SOTDTV) and then works by the remote's arrows and OK; this passes it the other buttons:
 * Back (closes the top pop-up, or the app), play/pause, rewind / fast-forward (the day or month before or after),
 * and Menu, which switches between the live site and the draft. OK held down is the page's "hold" (on a day: add it
 * to My playlist); a press of OK goes to the page as it was, once it's let go.
 * On opening it looks for a newer build (songs/tv.json, written with each build) and offers to install it.
 */
public class MainActivity extends Activity {
    static final String LIVE = "https://cooperking.github.io/songs/";
    static final String DRAFT = "https://cooperking.github.io/songs/draft/";
    static final String HOST = "cooperking.github.io";
    static final String TAG = "SOTD";
    static final String UPDATE_INFO = "https://cooperking.github.io/songs/tv.json";
    static final String UPDATE_APK = "https://cooperking.github.io/songs/tv.apk";

    WebView web;
    FrameLayout root;
    View full;   // a video shown full screen
    WebChromeClient.CustomViewCallback fullDone;
    SharedPreferences prefs;
    boolean fresh;   // clear the history once the page now loading has loaded
    final Handler later = new Handler(Looper.getMainLooper());
    KeyEvent okDown;   // OK pressed and not yet let go
    boolean okHeld;
    final Runnable okHold = new Runnable() { public void run() { if (okDown != null && !okHeld) { okHeld = true; page("hold"); } } };
    String pendingApk;   // an update waiting for this app to be allowed to install apps
    volatile boolean updating;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        prefs = getSharedPreferences("sotd", MODE_PRIVATE);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(0xec, 0x13, 0x5b));
        setContentView(root);
        makeWeb();
        web.requestFocus();
        if (saved != null) web.restoreState(saved);
        else web.loadUrl(home());
        new File(getCacheDir(), ApkProvider.NAME).delete();   // an update already installed (this is it)
        if (saved == null) later.postDelayed(new Runnable() { public void run() { checkForUpdate(); } }, 4000);
    }

    // ---- updates: the site says which build is newest (songs/tv.json); a newer one is offered, downloaded and handed
    // to the TV's installer, which asks to confirm. The first time, the TV has to allow this app to install apps. ----
    int myBuild() {
        try { return getPackageManager().getPackageInfo(getPackageName(), 0).versionCode; } catch (Exception e) { return Integer.MAX_VALUE; }
    }

    static String readAll(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] b = new byte[8192];
        for (int n; (n = in.read(b)) > 0; ) out.write(b, 0, n);
        in.close();
        return out.toString("UTF-8");
    }

    void checkForUpdate() {
        if (updating || System.currentTimeMillis() < prefs.getLong("laterUntil", 0)) return;
        new Thread(new Runnable() {
            public void run() {
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(UPDATE_INFO + "?t=" + System.currentTimeMillis()).openConnection();
                    c.setConnectTimeout(10000); c.setReadTimeout(10000); c.setUseCaches(false);
                    if (c.getResponseCode() != 200) { Log.w(TAG, "update check: HTTP " + c.getResponseCode()); return; }
                    JSONObject j = new JSONObject(readAll(c.getInputStream()));
                    final int latest = j.optInt("build", 0);
                    final String apk = j.optString("apk", UPDATE_APK);
                    Log.i(TAG, "update check: this is build " + myBuild() + ", newest " + latest);
                    if (latest > myBuild()) runOnUiThread(new Runnable() { public void run() { offerUpdate(latest, apk); } });
                } catch (Exception e) { Log.w(TAG, "update check: " + e); }
            }
        }).start();
    }

    void offerUpdate(final int latest, final String apk) {
        if (isFinishing() || updating) return;
        new AlertDialog.Builder(this)
            .setTitle("A new version of Song of the Day")
            .setMessage("Build " + latest + " is ready (this is build " + myBuild() + "). Install it now?")
            .setPositiveButton("Install", new DialogInterface.OnClickListener() {
                public void onClick(DialogInterface d, int w) { startUpdate(apk); }
            })
            .setNegativeButton("Later", new DialogInterface.OnClickListener() {
                public void onClick(DialogInterface d, int w) {   // asked again in a day
                    prefs.edit().putLong("laterUntil", System.currentTimeMillis() + 24L * 3600 * 1000).apply();
                }
            })
            .show();
    }

    boolean mayInstall() { return Build.VERSION.SDK_INT < 26 || getPackageManager().canRequestPackageInstalls(); }

    void startUpdate(String apk) {
        if (!mayInstall()) {   // the TV's switch for this app: Install unknown apps
            pendingApk = apk;   // kept, in case turning the switch on restarts the app
            prefs.edit().putString("pendingApk", apk).putLong("pendingUntil", System.currentTimeMillis() + 30L * 60 * 1000).apply();
            try {
                startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + getPackageName())));
                Toast.makeText(this, "Turn Song of the Day on, then press Back", Toast.LENGTH_LONG).show();
            } catch (ActivityNotFoundException e) {
                new AlertDialog.Builder(this)
                    .setTitle("Let the app install its update")
                    .setMessage("In Settings, go to Device & Software (or My Fire TV) \u2192 Developer options \u2192 Install unknown apps, "
                        + "and turn on Song of the Day. Then come back here.")
                    .setPositiveButton("OK", null)
                    .show();
            }
            return;
        }
        download(apk);
    }

    void download(final String apk) {
        updating = true;
        Toast.makeText(this, "Getting the new version\u2026", Toast.LENGTH_SHORT).show();
        new Thread(new Runnable() {
            public void run() {
                File part = new File(getCacheDir(), "update.part"), done = new File(getCacheDir(), ApkProvider.NAME);
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(apk + "?t=" + System.currentTimeMillis()).openConnection();
                    c.setConnectTimeout(15000); c.setReadTimeout(30000); c.setUseCaches(false);
                    if (c.getResponseCode() != 200) throw new IOException("HTTP " + c.getResponseCode());
                    InputStream in = c.getInputStream();
                    OutputStream out = new FileOutputStream(part);
                    byte[] b = new byte[65536];
                    for (int n; (n = in.read(b)) > 0; ) out.write(b, 0, n);
                    out.close(); in.close();
                    if (!part.renameTo(done)) throw new IOException("couldn't keep the download");
                    done.setReadable(true, false);
                    runOnUiThread(new Runnable() { public void run() { install(); } });
                } catch (final Exception e) {
                    Log.w(TAG, "update download: " + e);
                    runOnUiThread(new Runnable() { public void run() {
                        updating = false;
                        Toast.makeText(MainActivity.this, "Couldn't get the new version. It's at cooperking.github.io/songs/tv.apk", Toast.LENGTH_LONG).show();
                    } });
                }
            }
        }).start();
    }

    void install() {
        Intent i = new Intent(Intent.ACTION_VIEW);
        i.setDataAndType(Uri.parse("content://" + ApkProvider.AUTHORITY + "/" + ApkProvider.NAME), "application/vnd.android.package-archive");
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        try { startActivity(i); }
        catch (ActivityNotFoundException e) {
            Toast.makeText(this, "This TV won't install it from here. It's at cooperking.github.io/songs/tv.apk", Toast.LENGTH_LONG).show();
        }
        updating = false;
    }

    void makeWeb() {
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        WebView.setWebContentsDebuggingEnabled(true);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);   // My playlist and the other settings the page keeps
        s.setMediaPlaybackRequiresUserGesture(false);   // songs and videos start without a tap
        s.setUseWideViewPort(true);   // the page asks for a 1280-wide layout on a TV
        s.setLoadWithOverviewMode(true);
        s.setTextZoom(100);
        s.setSupportMultipleWindows(false);
        s.setUserAgentString(s.getUserAgentString() + " SOTDTV/1");
        web.setBackgroundColor(Color.rgb(0xec, 0x13, 0x5b));
        web.addJavascriptInterface(new Bridge(), "SOTDTV");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                if (!req.isForMainFrame()) return false;
                Uri u = req.getUrl();
                if (HOST.equals(u.getHost()) && u.getPath() != null && u.getPath().startsWith("/songs")) return false;
                // Spotify, YouTube and anything else: in the app that opens it, if there is one
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (ActivityNotFoundException e) {
                    Toast.makeText(MainActivity.this, "Nothing on this TV opens that link", Toast.LENGTH_SHORT).show();
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                Log.i(TAG, "loaded " + url);
                if (fresh) { fresh = false; view.clearHistory(); }
            }

            @Override
            public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail d) {
                // the page's process stopped (out of memory, say): a new view, the same page, rather than the app closing
                Log.w(TAG, "page process gone, crashed: " + d.didCrash());
                if (view != web) return true;
                String url = view.getUrl();
                root.removeView(view);
                view.destroy();
                makeWeb();
                web.loadUrl(url != null && url.startsWith("https://") ? url : home());
                web.requestFocus();
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (!req.isForMainFrame()) return;
                String back = req.getUrl().toString().replace("'", "%27");
                view.loadDataWithBaseURL(null,
                    "<html><body style=\"margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
                    + "background:#ec135b;color:#fff;font:24px sans-serif;text-align:center\"><div>Can't reach the site."
                    + "<br><br><button autofocus style=\"font:inherit;padding:12px 28px;border-radius:30px;border:0\" "
                    + "onclick=\"location.href='" + back + "'\">Try again</button></div></body></html>",
                    "text/html", "utf-8", null);
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onShowCustomView(View v, CustomViewCallback done) {
                if (full != null) { done.onCustomViewHidden(); return; }
                full = v; fullDone = done;
                root.addView(v, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
                web.setVisibility(View.GONE);
            }

            @Override
            public void onHideCustomView() { leaveFull(); }

            @Override
            public boolean onConsoleMessage(ConsoleMessage m) {
                if (m.messageLevel() == ConsoleMessage.MessageLevel.ERROR || m.messageLevel() == ConsoleMessage.MessageLevel.WARNING)
                    Log.w(TAG, "page: " + m.message() + " (" + m.sourceId() + ":" + m.lineNumber() + ")");
                return true;
            }

            @Override
            public Bitmap getDefaultVideoPoster() {   // no grey play button over a video before it starts
                return Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888);
            }
        });

    }

    String home() { return prefs.getBoolean("draft", false) ? DRAFT : LIVE; }

    void leaveFull() {
        if (full == null) return;
        root.removeView(full);
        full = null;
        web.setVisibility(View.VISIBLE);
        if (fullDone != null) fullDone.onCustomViewHidden();
        fullDone = null;
        web.requestFocus();
    }

    /** Called by the page. */
    class Bridge {
        @JavascriptInterface
        public void playing(final boolean on) {   // the screen stays on while something plays
            runOnUiThread(new Runnable() {
                public void run() {
                    if (on) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                    else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                }
            });
        }
    }

    void page(String k) { web.evaluateJavascript("window.sotdTV && sotdTV.key('" + k + "')", null); }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        int code = e.getKeyCode();
        boolean down = e.getAction() == KeyEvent.ACTION_DOWN, first = down && e.getRepeatCount() == 0;
        switch (code) {
            case KeyEvent.KEYCODE_BACK:
                if (e.getAction() == KeyEvent.ACTION_UP) {
                    if (full != null) { leaveFull(); return true; }
                    // the site closes its top pop-up (true), or has none open (false: the app closes); any other page
                    // (the advert help page, say) goes back to the one before it
                    web.evaluateJavascript("window.sotdTV ? sotdTV.back() : 'page'", new ValueCallback<String>() {
                        public void onReceiveValue(String r) {
                            if ("true".equals(r)) return;
                            if ("\"page\"".equals(r) && web.canGoBack()) web.goBack();
                            else finish();
                        }
                    });
                }
                return true;
            case KeyEvent.KEYCODE_DPAD_CENTER:
            case KeyEvent.KEYCODE_ENTER:
            case KeyEvent.KEYCODE_NUMPAD_ENTER:
                if (full != null) return super.dispatchKeyEvent(e);   // a video full screen: its own
                if (e.getAction() == KeyEvent.ACTION_DOWN) {
                    if (e.getRepeatCount() == 0) { okDown = new KeyEvent(e); okHeld = false; later.postDelayed(okHold, 600); }
                    else { later.removeCallbacks(okHold); okHold.run(); }   // the remote repeating: it's held
                    return true;
                }
                if (e.getAction() == KeyEvent.ACTION_UP) {
                    later.removeCallbacks(okHold);
                    if (!okHeld && okDown != null) { web.dispatchKeyEvent(okDown); web.dispatchKeyEvent(e); }   // a press
                    okDown = null;
                }
                return true;
            case KeyEvent.KEYCODE_MENU:
                if (first) {
                    boolean draft = !prefs.getBoolean("draft", false);
                    prefs.edit().putBoolean("draft", draft).apply();
                    Toast.makeText(this, draft ? "The draft" : "The live site", Toast.LENGTH_SHORT).show();
                    fresh = true;   // the other site starts a new history: Back doesn't return to this one
                    web.loadUrl(home());
                }
                return true;
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
            case KeyEvent.KEYCODE_MEDIA_PLAY:
            case KeyEvent.KEYCODE_MEDIA_PAUSE:
                if (first) page("playpause");
                return true;
            case KeyEvent.KEYCODE_MEDIA_FAST_FORWARD:
            case KeyEvent.KEYCODE_MEDIA_NEXT:
                if (first) page("next");
                return true;
            case KeyEvent.KEYCODE_MEDIA_REWIND:
            case KeyEvent.KEYCODE_MEDIA_PREVIOUS:
                if (first) page("prev");
                return true;
            default:
                return super.dispatchKeyEvent(e);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        if (pendingApk == null && System.currentTimeMillis() < prefs.getLong("pendingUntil", 0)) pendingApk = prefs.getString("pendingApk", null);
        if (pendingApk != null && mayInstall() && !updating) {   // back from allowing it
            String a = pendingApk; pendingApk = null;
            prefs.edit().remove("pendingApk").remove("pendingUntil").apply();
            download(a);
        }
    }

    @Override
    protected void onStop() {   // off the screen (Home, or another app): the music stops
        page("pause");
        web.onPause();
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        root.removeAllViews();
        web.destroy();
        super.onDestroy();
    }
}
