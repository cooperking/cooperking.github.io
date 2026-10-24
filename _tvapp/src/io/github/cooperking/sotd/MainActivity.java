package io.github.cooperking.sotd;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
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

/**
 * John's Song of the Day on a Fire TV: the site in a full-screen browser view. The page knows it's on a TV from
 * the browser's name (SOTDTV) and then works by the remote's arrows and OK; this passes it the other buttons:
 * Back (closes the top pop-up, or the app), play/pause, rewind / fast-forward (the day or month before or after),
 * and Menu, which switches between the live site and the draft.
 */
public class MainActivity extends Activity {
    static final String LIVE = "https://cooperking.github.io/songs/";
    static final String DRAFT = "https://cooperking.github.io/songs/draft/";
    static final String HOST = "cooperking.github.io";
    static final String TAG = "SOTD";

    WebView web;
    FrameLayout root;
    View full;   // a video shown full screen
    WebChromeClient.CustomViewCallback fullDone;
    SharedPreferences prefs;

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
            public void onPageFinished(WebView view, String url) { Log.i(TAG, "loaded " + url); }

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
                    web.evaluateJavascript("window.sotdTV ? sotdTV.back() : false", new ValueCallback<String>() {
                        public void onReceiveValue(String r) { if (!"true".equals(r)) finish(); }
                    });
                }
                return true;
            case KeyEvent.KEYCODE_MENU:
                if (first) {
                    boolean draft = !prefs.getBoolean("draft", false);
                    prefs.edit().putBoolean("draft", draft).apply();
                    Toast.makeText(this, draft ? "The draft" : "The live site", Toast.LENGTH_SHORT).show();
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
