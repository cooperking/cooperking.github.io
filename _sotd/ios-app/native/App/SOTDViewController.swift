import UIKit
import Capacitor

// The app's own view controller (Main.storyboard points here instead of Capacitor's): Capacitor's, with the app's own
// plugins added before the calendar loads, so the page finds them as it finds Capacitor's (window.Capacitor.Plugins).
class SOTDViewController: CAPBridgeViewController {
    private let cover = UIView()
    private var loading: NSKeyValueObservation?
    private var lift: DispatchWorkItem?

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
        // iPhone"): a dark cover over the web view while it loads, lifted once it has painted. (The web view
        // itself stays solid. A see-through one costs the iPhone more memory, and it then cleared the page more often:
        // John, 7 Nov 2026, "Still getting blackout reloads on iPhone a lot")
        let ground = UIColor(red: 0x1b / 255.0, green: 0x10 / 255.0, blue: 0x20 / 255.0, alpha: 1)
        view.backgroundColor = ground
        if let web = bridge?.webView {
            web.isOpaque = true
            web.scrollView.backgroundColor = ground
            cover.backgroundColor = ground
            cover.isUserInteractionEnabled = false
            cover.frame = web.bounds
            cover.autoresizingMask = [.flexibleWidth, .flexibleHeight]
            web.addSubview(cover)
            loading = web.observe(\.isLoading, options: [.initial, .new]) { [weak self] w, _ in
                DispatchQueue.main.async { self?.coverFor(w.isLoading) }
            }
        }
    }

    // shown at once when a load starts (a reload after the iPhone cleared the page too); lifted a moment after it ends,
    // when the page has painted its month's colour
    private func coverFor(_ isLoading: Bool) {
        lift?.cancel()
        if isLoading {
            cover.layer.removeAllAnimations(); cover.alpha = 1; cover.isHidden = false
            if let web = bridge?.webView { web.bringSubviewToFront(cover) }
            return
        }
        let w = DispatchWorkItem { [weak self] in
            guard let c = self?.cover else { return }
            UIView.animate(withDuration: 0.15, animations: { c.alpha = 0 }, completion: { done in if done { c.isHidden = true } })
        }
        lift = w
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: w)
    }
}
