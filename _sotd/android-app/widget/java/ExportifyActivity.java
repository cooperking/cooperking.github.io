package io.github.cooperking.sotd;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.util.Base64;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;

/**
 * Exportify inside the app (John, 2 Nov 2026: as the iPhone app's, "the app catches the download directly"). The app is
 * a Chrome app (a Trusted Web Activity), so Import from Spotify opens this screen with an intent:// link
 * (sotdapp://exportify, extras c: a one-time code, k: the member's sign-in). Exportify makes its .zip or .csv in the page
 * (a blob: link with download=), which a WebView can't save by itself: a little script catches that link, reads the
 * file and hands it here; it goes to the Worker (/members/impdrop?c=), and the screen closes. The calendar, back in
 * front, takes it from there. The Spotify sign-in is kept (the WebView's cookies). Spotify's "Continue with Google"
 * can't sign in inside an app (Google refuses embedded views): Safari's equivalent here is Chrome, from the ⋮ button.
 */
public class ExportifyActivity extends Activity {
    static final String WORKER = "https://sotd-playlists.cooperking.workers.dev";
    static final String START = "https://exportify.app/";
    WebView web;
    ProgressBar spin;
    String code, key;
    boolean sending;

    static final String CATCH = "(function(){if(window.__sotdDl)return;window.__sotdDl=1;"
        + "function send(h,n){SotdDl.busy();fetch(h).then(function(r){return r.blob();}).then(function(b){var f=new FileReader();"
        + "f.onload=function(){SotdDl.got(n||'',b.type||'',String(f.result).split(',')[1]||'');};f.onerror=function(){SotdDl.fail('read');};f.readAsDataURL(b);})"
        + ".catch(function(e){SotdDl.fail(String(e));});}"
        + "function hit(a){if(!a||!a.href)return false;var h=a.href;if(!/^(blob:|data:)/.test(h))return false;send(h,a.getAttribute('download')||'');return true;}"
        + "var P=HTMLAnchorElement.prototype,oc=P.click,od=P.dispatchEvent;"
        + "P.click=function(){if(hit(this))return;return oc.apply(this,arguments);};"
        + "P.dispatchEvent=function(e){if(e&&e.type==='click'&&hit(this))return true;return od.apply(this,arguments);};"
        + "document.addEventListener('click',function(e){var a=e.target&&e.target.closest&&e.target.closest('a[href]');if(a&&hit(a)){e.preventDefault();e.stopPropagation();}},true);"
        + "window.__sotdSend=send;})();";

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Intent in = getIntent();
        code = in.getStringExtra("c");
        key = in.getStringExtra("k");
        if (code == null || !code.matches("[A-Za-z0-9]{16,40}") || key == null || key.isEmpty()) { finish(); return; }

        int pink = Color.parseColor("#EC135B");
        getWindow().setStatusBarColor(pink);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(pink);
        int pad = dp(8);
        bar.setPadding(pad, pad, pad, pad);
        Button close = new Button(this);
        close.setText("Close");
        close.setAllCaps(false);
        close.setTextColor(Color.WHITE);
        close.setBackgroundColor(Color.TRANSPARENT);
        close.setOnClickListener(v -> finish());
        TextView title = new TextView(this);
        title.setText("Exportify");
        title.setTextColor(Color.WHITE);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        title.setGravity(Gravity.CENTER);
        spin = new ProgressBar(this);
        spin.setIndeterminate(true);
        spin.setVisibility(View.INVISIBLE);
        Button chrome = new Button(this);
        chrome.setText("Chrome");
        chrome.setAllCaps(false);
        chrome.setTextColor(Color.WHITE);
        chrome.setBackgroundColor(Color.TRANSPARENT);
        chrome.setOnClickListener(v -> {
            try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(web.getUrl() != null ? web.getUrl() : START))); } catch (Exception e) { }
        });
        bar.addView(close, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        bar.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        bar.addView(spin, new LinearLayout.LayoutParams(dp(28), dp(28)));
        bar.addView(chrome, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(bar, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setSupportMultipleWindows(false);   // (window.open: in this same view)
        s.setJavaScriptCanOpenWindowsAutomatically(true);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);
        web.addJavascriptInterface(new Bridge(), "SotdDl");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                String sch = u.getScheme() == null ? "" : u.getScheme().toLowerCase();
                if (sch.equals("http") || sch.equals("https") || sch.equals("blob") || sch.equals("data") || sch.equals("about")) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception e) { }   // (spotify: and the like, to their app)
                return true;
            }
            @Override
            public void onPageStarted(WebView v, String url, android.graphics.Bitmap f) { if (!sending) spin.setVisibility(View.VISIBLE); }
            @Override
            public void onPageFinished(WebView v, String url) {
                if (!sending) spin.setVisibility(View.INVISIBLE);
                v.evaluateJavascript(CATCH, null);
                CookieManager.getInstance().flush();
            }
        });
        web.setDownloadListener((url, ua, disp, mime, len) -> {   // (one the script missed: a blob: link it can still read)
            if (url != null && (url.startsWith("blob:") || url.startsWith("data:"))) {
                String n = android.webkit.URLUtil.guessFileName(url, disp, mime);
                web.evaluateJavascript(CATCH + "window.__sotdSend(" + jsStr(url) + "," + jsStr(n) + ");", null);
            }
        });
        root.addView(web, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(root);
        if (b != null) web.restoreState(b); else web.loadUrl(START);
    }

    @Override
    protected void onSaveInstanceState(Bundle out) { super.onSaveInstanceState(out); if (web != null) web.saveState(out); }

    @Override
    public void onBackPressed() { if (web != null && web.canGoBack()) web.goBack(); else super.onBackPressed(); }

    @Override
    protected void onDestroy() { if (web != null) { web.destroy(); web = null; } super.onDestroy(); }

    int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    static String jsStr(String s) { return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "") + "'"; }

    class Bridge {
        @JavascriptInterface
        public void busy() { runOnUiThread(() -> { sending = true; spin.setVisibility(View.VISIBLE); }); }

        @JavascriptInterface
        public void fail(String why) { runOnUiThread(() -> { sending = false; spin.setVisibility(View.INVISIBLE); Toast.makeText(ExportifyActivity.this, "That file couldn’t be read. Try again.", Toast.LENGTH_LONG).show(); }); }

        @JavascriptInterface
        public void got(String name, String type, String b64) {
            final byte[] data;
            try { data = Base64.decode(b64, Base64.DEFAULT); } catch (Exception e) { fail("decode"); return; }
            if (data.length == 0 || data.length > 40_000_000) { fail("size"); return; }
            String n = name == null || name.isEmpty() ? "playlists" : name;
            final String fname = n.matches("(?i).*\\.(zip|csv)$") ? n : n + ((type != null && type.contains("zip")) || (data.length > 1 && data[0] == 'P' && data[1] == 'K') ? ".zip" : ".csv");
            new Thread(() -> {
                boolean ok = false;
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(WORKER + "/members/impdrop?c=" + code).openConnection();
                    c.setRequestMethod("POST");
                    c.setDoOutput(true);
                    c.setConnectTimeout(20000);
                    c.setReadTimeout(60000);
                    c.setFixedLengthStreamingMode(data.length);
                    c.setRequestProperty("Authorization", "Bearer " + key);
                    c.setRequestProperty("Content-Type", fname.toLowerCase().endsWith(".zip") ? "application/zip" : "text/csv");
                    c.setRequestProperty("X-Name", URLEncoder.encode(fname, "UTF-8"));
                    c.setRequestProperty("X-App", "android-exportify");
                    try (OutputStream o = c.getOutputStream()) { o.write(data); }
                    ok = c.getResponseCode() == 200;
                    c.disconnect();
                } catch (Exception e) { ok = false; }
                final boolean done = ok;
                runOnUiThread(() -> {
                    sending = false;
                    spin.setVisibility(View.INVISIBLE);
                    if (done) { Toast.makeText(ExportifyActivity.this, "Got your Exportify file", Toast.LENGTH_SHORT).show(); finish(); }
                    else Toast.makeText(ExportifyActivity.this, "It didn’t reach Song of the Day. Check the connection and try again.", Toast.LENGTH_LONG).show();
                });
            }).start();
        }
    }
}
