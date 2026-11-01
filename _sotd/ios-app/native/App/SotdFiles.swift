import Foundation
import Capacitor

// A file given to the app (John, 1 Nov 2026: "the apps available as a way to open the downloaded file"): the app is
// listed for .zip and .csv files (customise.sh: CFBundleDocumentTypes), so Exportify's download can be opened with it
// from Files or Safari's downloads. iOS copies the file into the app's own folder and hands the app its file:// address,
// which the calendar hears from Capacitor's App plugin (appUrlOpen, or getLaunchUrl when it starts the app); read({ url })
// gives the page { name, data: the file in base64 }, for Import from Spotify. Only files inside the app's own folders,
// 80 MB at most; the copy is deleted once read. From build 28.
@objc(SotdFilesPlugin)
public class SotdFilesPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "SotdFilesPlugin"
    public let jsName = "SotdFiles"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "read", returnType: CAPPluginReturnPromise)
    ]

    @objc func read(_ call: CAPPluginCall) {
        guard let s = call.getString("url"), let url = URL(string: s), url.isFileURL else { call.reject("not a file"); return }
        let ext = url.pathExtension.lowercased()
        guard ext == "zip" || ext == "csv" else { call.reject("not a zip or csv"); return }
        let path = url.standardizedFileURL.resolvingSymlinksInPath().path
        let home = URL(fileURLWithPath: NSHomeDirectory()).standardizedFileURL.resolvingSymlinksInPath().path
        let tmp = FileManager.default.temporaryDirectory.standardizedFileURL.resolvingSymlinksInPath().path
        guard path.hasPrefix(home + "/") || path.hasPrefix(tmp) else { call.reject("not the app's file"); return }
        DispatchQueue.global(qos: .userInitiated).async {
            let open = url.startAccessingSecurityScopedResource()
            defer { if open { url.stopAccessingSecurityScopedResource() } }
            guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
                  let size = attrs[.size] as? NSNumber, size.intValue <= 80_000_000,
                  let data = try? Data(contentsOf: url) else { call.reject("couldn't read it"); return }
            try? FileManager.default.removeItem(at: url)   // (iOS's copy, in the app's Inbox)
            call.resolve(["name": url.lastPathComponent, "data": data.base64EncodedString()])
        }
    }
}
