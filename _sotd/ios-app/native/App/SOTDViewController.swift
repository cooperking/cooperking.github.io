import UIKit
import Capacitor

// The app's own view controller (Main.storyboard points here instead of Capacitor's): Capacitor's, with the app's own
// plugins added before the calendar loads, so the page finds them as it finds Capacitor's (window.Capacitor.Plugins).
class SOTDViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(SotdCastPlugin())
        bridge?.registerPluginInstance(SotdSiriPlugin())
        bridge?.registerPluginInstance(SotdContactsPlugin())   // (Apple's contact picker, for the Members tab: build 22 on)
        bridge?.registerPluginInstance(SotdIconPlugin())   // (the icon in the month's colours: build 29 on)
        bridge?.registerPluginInstance(SotdFilesPlugin())   // (a .zip or .csv opened with the app, for Import from Spotify: build 28 on)
        bridge?.registerPluginInstance(SotdExportifyPlugin())   // (Exportify inside the app, its download taken straight in)
        bridge?.registerPluginInstance(SotdSpotifyPlugin())   // (Spotify on this phone, woken through Spotify's iPhone toolkit: build 32 on)
        bridge?.registerPluginInstance(SotdBgPlugin())   // (the app's own player for the hand-over from Spotify: build 41 on)
        SotdGuard.shared.attach(bridge?.webView)   // (away, playing: stopped when another device begins: build 25 on)
        // never white while the page loads or reloads (John, 5 Nov 2026: "Page flashes white on load occasionally on
        // iPhone"): the web view see-through, over the app's dark ground, until the page paints its month's colour
        let ground = UIColor(red: 0x1b / 255.0, green: 0x10 / 255.0, blue: 0x20 / 255.0, alpha: 1)
        view.backgroundColor = ground
        if let web = bridge?.webView {
            web.isOpaque = false
            web.backgroundColor = ground
            web.scrollView.backgroundColor = ground
            if #available(iOS 15.0, *) { web.underPageBackgroundColor = ground }
        }
    }
}
