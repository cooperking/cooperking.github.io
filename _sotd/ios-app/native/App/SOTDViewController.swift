import UIKit
import Capacitor

// The app's own view controller (Main.storyboard points here instead of Capacitor's): Capacitor's, with the app's own
// plugins added before the calendar loads, so the page finds them as it finds Capacitor's (window.Capacitor.Plugins).
class SOTDViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(SotdCastPlugin())
        bridge?.registerPluginInstance(SotdSiriPlugin())
    }
}
