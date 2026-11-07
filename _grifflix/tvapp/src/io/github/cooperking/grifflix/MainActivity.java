package io.github.cooperking.grifflix;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.DialogInterface;
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
import android.text.InputFilter;
import android.text.InputType;
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
import android.widget.EditText;
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
 * Grifflix on a Fire TV: the Grifflix page full screen, worked by the remote. Made from the Song of the Day TV app
 * (_tvapp), without its YouTube parts. The page knows it's on a TV from the browser's name (GRIFFLIXTV) and moves
 * between buttons with the arrows itself; OK reaches it as a key. This passes it the buttons it can't hear:
 * Back (window.grifflixTV.back(): closes the player or the top pop-up; 'page' when there's nothing to close, and
 * then the app closes), play/pause, rewind and fast-forward (grifflixTV.key). Menu: sign in with a code, and look
 * for a new version. The screen stays on while a film plays (the page says so through GRIFFLIXTV.playing).
 * Updates: grifflix/tv.json names the newest build (written by each build); a newer one is offered, downloaded and
 * handed to the TV's installer, as the Song of the Day app does.
 */
public class MainActivity extends Activity {
    static final String HOME = "https://cooperking.github.io/grifflix/";
    static final String HOST = "cooperking.github.io";
    static final String TAG = "GRIFFLIX";
    static final String UPDATE_INFO = "https://cooperking.github.io/grifflix/tv.json";
    static final String UPDATE_APK = "https://cooperking.github.io/grifflix/tv.apk";
    static final int GROUND = Color.BLACK;   // (black behind the page as it loads, as John asked for the Fire TV)

    WebView web;
    FrameLayout root;
    View full;   // a video the page put full screen
    WebChromeClient.CustomViewCallback fullDone;
    SharedPreferences prefs;
    final Handler later = new Handler(Looper.getMainLooper());
    String pendingApk;
    volatile boolean updating;
    boolean handedOver, offering;
    long lastCheck;
    final Runnable autoCheck = new Runnable() { public void run() { checkForUpdate(false); } };

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        prefs = getSharedPreferences("grifflix", MODE_PRIVATE);
        root = new FrameLayout(this);
        root.setBackgroundColor(GROUND);
        setContentView(root);
        makeWeb();
        web.requestFocus();
        if (saved != null) web.restoreState(saved); else web.loadUrl(HOME);
        new File(getCacheDir(), ApkProvider.NAME).delete();   // an update already installed (this is it)
    }

    void makeWeb() {
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        WebView.setWebContentsDebuggingEnabled(true);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);   // the sign-in and the page's settings
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setTextZoom(100);
        s.setSupportMultipleWindows(false);
        s.setUserAgentString(s.getUserAgentString() + " GRIFFLIXTV/1");
        web.setBackgroundColor(GROUND);
        web.addJavascriptInterface(new Bridge(), "GRIFFLIXTV");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                if (!req.isForMainFrame()) return false;
                Uri u = req.getUrl();
                if (HOST.equals(u.getHost()) && u.getPath() != null && (u.getPath().startsWith("/grifflix") || u.getPath().startsWith("/songs"))) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); }
                catch (ActivityNotFoundException e) { Toast.makeText(MainActivity.this, "Nothing on this TV opens that link", Toast.LENGTH_SHORT).show(); }
                return true;
            }

            @Override
            public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail d) {   // the page's process stopped: a new view, not a closed app
                if (view != web) return true;
                String url = view.getUrl();
                root.removeView(view);
                view.destroy();
                makeWeb();
                web.loadUrl(url != null && url.startsWith("https://") ? url : HOME);
                web.requestFocus();
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (!req.isForMainFrame()) return;
                String back = req.getUrl().toString().replace("'", "%27");
                view.loadDataWithBaseURL(null,
                    "<html><body style=\"margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
                    + "background:#000;color:#fffaf5;font:24px Georgia,serif;text-align:center\"><div>Can't reach Grifflix just now."
                    + "<br><br><button autofocus style=\"font:18px sans-serif;padding:12px 28px;border-radius:30px;border:0\" "
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
            public Bitmap getDefaultVideoPoster() { return Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888); }
        });
    }

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
        public void playing(final boolean on) {   // the screen stays on while a film plays
            runOnUiThread(new Runnable() {
                public void run() {
                    if (on) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                    else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                }
            });
        }

        @JavascriptInterface
        public int build() { return myBuild(); }
    }

    void page(String k) { web.evaluateJavascript("window.grifflixTV && grifflixTV.key('" + k + "')", null); }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        boolean first = e.getAction() == KeyEvent.ACTION_DOWN && e.getRepeatCount() == 0;
        switch (e.getKeyCode()) {
            case KeyEvent.KEYCODE_BACK:
                if (e.getAction() == KeyEvent.ACTION_UP) {
                    if (full != null) { leaveFull(); return true; }
                    web.evaluateJavascript("window.grifflixTV ? grifflixTV.back() : 'page'", new ValueCallback<String>() {
                        public void onReceiveValue(String r) {
                            if ("\"done\"".equals(r)) return;
                            if (web.canGoBack() && !"\"page\"".equals(r)) web.goBack();
                            else finish();
                        }
                    });
                }
                return true;
            case KeyEvent.KEYCODE_MENU:
                if (first) menu();
                return true;
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
            case KeyEvent.KEYCODE_MEDIA_PLAY:
            case KeyEvent.KEYCODE_MEDIA_PAUSE:
                if (first) page("play");
                return true;
            case KeyEvent.KEYCODE_MEDIA_FAST_FORWARD:
            case KeyEvent.KEYCODE_MEDIA_NEXT:
                if (first) page("ff");
                return true;
            case KeyEvent.KEYCODE_MEDIA_REWIND:
            case KeyEvent.KEYCODE_MEDIA_PREVIOUS:
                if (first) page("rew");
                return true;
            default:
                return super.dispatchKeyEvent(e);
        }
    }

    void menu() {
        String[] items = { "Sign in with a code", "Check for a new version", "Reload" };
        new AlertDialog.Builder(this)
            .setTitle("Grifflix · build " + myBuild())
            .setItems(items, new DialogInterface.OnClickListener() {
                public void onClick(DialogInterface d, int which) {
                    if (which == 0) codeSignIn();
                    else if (which == 1) checkForUpdate(true);
                    else web.reload();
                }
            })
            .show();
    }

    void codeSignIn() {
        final EditText code = new EditText(this);
        code.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        code.setFilters(new InputFilter[] { new InputFilter.AllCaps(), new InputFilter.LengthFilter(12) });
        code.setHint("e.g. K7MP2Q");
        code.setTextSize(28);
        FrameLayout box = new FrameLayout(this);
        int pad = (int) (24 * getResources().getDisplayMetrics().density);
        box.setPadding(pad, pad / 2, pad, 0);
        box.addView(code);
        new AlertDialog.Builder(this)
            .setTitle("Sign in")
            .setMessage("Type the code from your Profile on Song of the Day (on your phone: tap your person icon, then Get a code).")
            .setView(box)
            .setPositiveButton("Sign in", new DialogInterface.OnClickListener() {
                public void onClick(DialogInterface d, int w) {
                    String c = code.getText().toString().toUpperCase().replaceAll("[^A-Z0-9]", "");
                    if (c.length() != 6) { Toast.makeText(MainActivity.this, "A code has 6 letters and numbers", Toast.LENGTH_SHORT).show(); return; }
                    web.evaluateJavascript("typeof grifflixJoin === 'function' ? (grifflixJoin('" + c + "'), 'ok') : 'no'", null);
                }
            })
            .setNegativeButton("Cancel", null)
            .show();
        code.requestFocus();
    }

    // ---- updates (as the Song of the Day app: tv.json says which build is newest) ----
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

    void checkForUpdate(final boolean asked) {
        lastCheck = System.currentTimeMillis();
        if (updating || offering || (!asked && System.currentTimeMillis() < prefs.getLong("laterUntil", 0))) return;
        new Thread(new Runnable() {
            public void run() {
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(UPDATE_INFO + "?t=" + System.currentTimeMillis()).openConnection();
                    c.setConnectTimeout(10000); c.setReadTimeout(10000); c.setUseCaches(false);
                    if (c.getResponseCode() != 200) return;
                    JSONObject j = new JSONObject(readAll(c.getInputStream()));
                    final int latest = j.optInt("build", 0);
                    final String apk = j.optString("apk", UPDATE_APK);
                    if (latest > myBuild()) runOnUiThread(new Runnable() { public void run() { offerUpdate(latest, apk); } });
                    else if (asked) runOnUiThread(new Runnable() { public void run() {
                        Toast.makeText(MainActivity.this, "This is the newest version (build " + myBuild() + ")", Toast.LENGTH_LONG).show(); } });
                } catch (final Exception e) {
                    Log.w(TAG, "update check: " + e);
                    if (asked) runOnUiThread(new Runnable() { public void run() {
                        Toast.makeText(MainActivity.this, "Couldn't check for a new version: " + e.getMessage(), Toast.LENGTH_LONG).show(); } });
                }
            }
        }).start();
    }

    void offerUpdate(final int latest, final String apk) {
        if (isFinishing() || updating || offering) return;
        offering = true;
        new AlertDialog.Builder(this)
            .setOnDismissListener(new DialogInterface.OnDismissListener() { public void onDismiss(DialogInterface d) { offering = false; } })
            .setTitle("A new version of Grifflix")
            .setMessage("Build " + latest + " is ready (this is build " + myBuild() + "). Install it now?")
            .setPositiveButton("Install", new DialogInterface.OnClickListener() { public void onClick(DialogInterface d, int w) { startUpdate(apk); } })
            .setNegativeButton("Later", new DialogInterface.OnClickListener() {
                public void onClick(DialogInterface d, int w) { prefs.edit().putLong("laterUntil", System.currentTimeMillis() + 24L * 3600 * 1000).apply(); }
            })
            .show();
    }

    boolean mayInstall() { return Build.VERSION.SDK_INT < 26 || getPackageManager().canRequestPackageInstalls(); }

    void startUpdate(String apk) {
        if (!mayInstall()) {
            pendingApk = apk;
            prefs.edit().putString("pendingApk", apk).putLong("pendingUntil", System.currentTimeMillis() + 30L * 60 * 1000).apply();
            try {
                startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + getPackageName())));
                Toast.makeText(this, "Turn Grifflix on, then open Grifflix again", Toast.LENGTH_LONG).show();
            } catch (ActivityNotFoundException e) {
                new AlertDialog.Builder(this)
                    .setTitle("Let the app install its update")
                    .setMessage("In Settings, go to My Fire TV → Developer options → Install unknown apps, and turn on Grifflix. Then open Grifflix again.")
                    .setPositiveButton("OK", null)
                    .show();
            }
            return;
        }
        download(apk);
    }

    void download(final String apk) {
        updating = true;
        Toast.makeText(this, "Getting the new version…", Toast.LENGTH_SHORT).show();
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
                        Toast.makeText(MainActivity.this, "Couldn't get the new version. It's at cooperking.github.io/grifflix/tv.apk", Toast.LENGTH_LONG).show();
                    } });
                }
            }
        }).start();
    }

    void install() {
        Intent i = new Intent(Intent.ACTION_VIEW);
        i.setDataAndType(Uri.parse("content://" + ApkProvider.AUTHORITY + "/" + ApkProvider.NAME), "application/vnd.android.package-archive");
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        try { startActivity(i); handedOver = true; }
        catch (ActivityNotFoundException e) {
            updating = false;
            Toast.makeText(this, "This TV won't install it from here. It's at cooperking.github.io/grifflix/tv.apk", Toast.LENGTH_LONG).show();
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle out) { super.onSaveInstanceState(out); web.saveState(out); }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        if (System.currentTimeMillis() - lastCheck > 20L * 60 * 1000) { lastCheck = System.currentTimeMillis(); later.postDelayed(autoCheck, 3000); }
        if (handedOver) { handedOver = false; updating = false; }
        if (pendingApk == null && System.currentTimeMillis() < prefs.getLong("pendingUntil", 0)) pendingApk = prefs.getString("pendingApk", null);
        if (pendingApk != null && mayInstall() && !updating) {
            String a = pendingApk; pendingApk = null;
            prefs.edit().remove("pendingApk").remove("pendingUntil").apply();
            download(a);
        }
    }

    @Override
    protected void onStop() {   // off the screen (Home, or another app): the film pauses, and its place is kept
        web.evaluateJavascript("(function(){var v=document.getElementById('vid'); if (v && !v.paused) v.pause();})()", null);
        web.onPause();
        super.onStop();
    }

    @Override
    protected void onDestroy() { root.removeAllViews(); web.destroy(); super.onDestroy(); }
}
