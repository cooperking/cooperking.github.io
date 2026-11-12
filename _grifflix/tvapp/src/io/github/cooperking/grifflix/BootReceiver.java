package io.github.cooperking.grifflix;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** The Fire TV started (or Grifflix updated): Play on the TV's listener starts too, so a film sent from a phone opens Grifflix. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent i) {
        String a = i.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(a) || Intent.ACTION_MY_PACKAGE_REPLACED.equals(a)) TvListener.start(c);
    }
}
