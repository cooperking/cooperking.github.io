import UIKit
import Capacitor

// The app's icon in each month's colours (John, 1 Nov 2026: "on the iPhone app the icon should update to the colours each
// month as it does on the homescreen app"). The twelve icons are built into the app as alternate icons (customise.sh,
// icons.py --ios: AppIcon-M01 to AppIcon-M12); the calendar asks for the month's when the app opens in a new month.
// get() gives { name: the icon now ('' the app's own), can }; set({ name }) changes it. iOS says so in a small message
// each time (it always does when an app changes its icon). From build 29.
@objc(SotdIconPlugin)
public class SotdIconPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "SotdIconPlugin"
    public let jsName = "SotdIcon"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "get", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "set", returnType: CAPPluginReturnPromise)
    ]

    @objc func get(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            call.resolve(["name": UIApplication.shared.alternateIconName ?? "", "can": UIApplication.shared.supportsAlternateIcons])
        }
    }

    @objc func set(_ call: CAPPluginCall) {
        let name = call.getString("name") ?? ""
        guard name.isEmpty || name.range(of: "^AppIcon-M(0[1-9]|1[0-2])$", options: .regularExpression) != nil else { call.reject("no such icon"); return }
        DispatchQueue.main.async {
            let app = UIApplication.shared
            guard app.supportsAlternateIcons else { call.reject("not here"); return }
            if (app.alternateIconName ?? "") == name { call.resolve(["name": name]); return }
            guard app.applicationState == .active else { call.reject("not now"); return }
            app.setAlternateIconName(name.isEmpty ? nil : name) { err in
                if let e = err { call.reject(e.localizedDescription) } else { call.resolve(["name": name]) }
            }
        }
    }
}
