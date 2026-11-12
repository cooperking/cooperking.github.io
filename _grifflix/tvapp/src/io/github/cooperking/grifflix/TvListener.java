package io.github.cooperking.grifflix;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Play on the TV with Grifflix closed: a small listener that waits at the Mac mini for a phone's word, as the page does while
 * it's open, and opens Grifflix to play the film when one comes (John, 12 Nov 2026: "Is there any way to have it automatically
 * open the app on the fire tv if it's not open?"). It steps aside while Grifflix is on the screen (the page listens then), starts
 * with the Fire TV, and starts again if Fire OS stops it. The TV must be on: a Fire TV asleep can't be woken from here.
 */
public class TvListener extends Service {
    static final String TAG = "GRIFFLIX";
    static volatile boolean appVisible;   // (Grifflix on the screen: the page itself listens)
    static volatile String pendingCmd;    // (a film sent while it was closed, for the page to play once it's open)
    volatile boolean running;
    Thread loop;

    static boolean ready(Context c) {
        SharedPreferences p = c.getSharedPreferences("grifflix", MODE_PRIVATE);
        return p.getString("tv_gw", null) != null && p.getString("tv_tok", null) != null && p.getString("tv_dev", null) != null;
    }

    static void start(Context c) {   // (from the app, or at the Fire TV's start)
        if (!ready(c)) return;
        Intent i = new Intent(c, TvListener.class);
        try {
            if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i); else c.startService(i);
        } catch (Exception e) { Log.w(TAG, "listener not started", e); }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        Notification n;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            nm.createNotificationChannel(new NotificationChannel("listen", "Play on the TV", NotificationManager.IMPORTANCE_MIN));
            n = new Notification.Builder(this, "listen").setContentTitle("Grifflix").setContentText("Ready for Play on the TV")
                .setSmallIcon(R.mipmap.ic_launcher).setOngoing(true).build();
        } else {
            n = new Notification.Builder(this).setContentTitle("Grifflix").setContentText("Ready for Play on the TV")
                .setSmallIcon(R.mipmap.ic_launcher).setOngoing(true).setPriority(Notification.PRIORITY_MIN).build();
        }
        try {
            if (Build.VERSION.SDK_INT >= 29) startForeground(7, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            else startForeground(7, n);
        } catch (Exception e) { Log.w(TAG, "listener in the background only", e); }
        running = true;
        loop = new Thread(new Runnable() { public void run() { listen(); } }, "grifflix-listen");
        loop.start();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) { return START_STICKY; }   // (stopped by Fire OS: started again)

    @Override
    public void onDestroy() { running = false; if (loop != null) loop.interrupt(); super.onDestroy(); }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    void listen() {
        while (running) {
            try {
                SharedPreferences p = getSharedPreferences("grifflix", MODE_PRIVATE);
                String gw = p.getString("tv_gw", null), tok = p.getString("tv_tok", null), dev = p.getString("tv_dev", null), name = p.getString("tv_name", "Fire TV");
                if (appVisible || gw == null || tok == null || dev == null) { Thread.sleep(3000); continue; }
                JSONObject body = new JSONObject();
                body.put("dev", dev); body.put("name", name); body.put("wait", 20);
                HttpURLConnection c = (HttpURLConnection) new URL(gw + "/api/tvhere").openConnection();
                c.setConnectTimeout(15000); c.setReadTimeout(35000);
                c.setRequestMethod("POST"); c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setRequestProperty("Authorization", "Bearer " + tok);
                OutputStream o = c.getOutputStream(); o.write(body.toString().getBytes("UTF-8")); o.close();
                int code = c.getResponseCode();
                if (code == 401 || code == 403) { c.disconnect(); Thread.sleep(60000); continue; }   // (signed out: quietly, now and then)
                if (code != 200) { c.disconnect(); Thread.sleep(15000); continue; }
                InputStream in = c.getInputStream(); ByteArrayOutputStream b = new ByteArrayOutputStream(); byte[] buf = new byte[4096]; int k;
                while ((k = in.read(buf)) > 0) b.write(buf, 0, k);
                in.close(); c.disconnect();
                JSONArray cmds = new JSONObject(b.toString("UTF-8")).optJSONArray("cmds");
                for (int i = 0; cmds != null && i < cmds.length(); i++) {
                    JSONObject cmd = cmds.getJSONObject(i);
                    if ("play".equals(cmd.optString("cmd")) && !appVisible) open(cmd.toString());   // (only a film to play opens it; pause and the like need it open)
                }
                Thread.sleep(200);
            } catch (InterruptedException e) {
                return;
            } catch (Exception e) {
                Log.w(TAG, "listening: " + e);
                try { Thread.sleep(15000); } catch (InterruptedException x) { return; }
            }
        }
    }

    void open(String cmd) {
        pendingCmd = cmd;
        Intent i = new Intent(this, MainActivity.class);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
        i.putExtra("tvcmd", true);
        try { startActivity(i); } catch (Exception e) { Log.w(TAG, "couldn't open Grifflix (Display over other apps?)", e); }
    }
}
