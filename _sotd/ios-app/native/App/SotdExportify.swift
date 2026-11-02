import UIKit
import WebKit
import Capacitor

// Exportify inside the app (John, 2 Nov 2026: skip the share step: "open Exportify inside the app itself, so the app
// catches the download directly"). open({ url }) shows Exportify full screen in the app's own web view, with Close at
// the top and Safari as a way out (Spotify's "Continue with Google" won't sign in inside an app). When Exportify saves
// its .zip or .csv, the web view hands the download to the app instead of to Files: the screen closes and the page
// hears "file" { name, data: the file in base64 }, for Import from Spotify. "closed" { file: true/false } as it goes.
// The Spotify sign-in is kept (the default website data store), so it's asked for once. From the build after 2 Nov 2026.
@objc(SotdExportifyPlugin)
public class SotdExportifyPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "SotdExportifyPlugin"
    public let jsName = "SotdExportify"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "open", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "close", returnType: CAPPluginReturnPromise)
    ]
    private weak var shown: SotdExportifyController?

    @objc func open(_ call: CAPPluginCall) {
        let s = call.getString("url") ?? "https://exportify.app/"
        guard let url = URL(string: s), url.scheme == "https" else { call.reject("not an https address"); return }
        let tint = call.getString("color")
        DispatchQueue.main.async {
            guard let host = self.bridge?.viewController else { call.reject("no view"); return }
            if self.shown != nil { call.resolve(); return }
            let c = SotdExportifyController(start: url, tint: tint)
            c.onFile = { [weak self] name, data in
                self?.notifyListeners("file", data: ["name": name, "data": data.base64EncodedString()])
            }
            c.onClose = { [weak self] got in self?.notifyListeners("closed", data: ["file": got]) }
            let nav = UINavigationController(rootViewController: c)
            nav.modalPresentationStyle = .fullScreen
            self.shown = c
            host.present(nav, animated: true) { call.resolve() }
        }
    }

    @objc func close(_ call: CAPPluginCall) {
        DispatchQueue.main.async { self.shown?.finish(got: false); call.resolve() }
    }
}

class SotdExportifyController: UIViewController, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    let start: URL
    let tint: UIColor?
    var web: WKWebView!
    var onFile: ((String, Data) -> Void)?
    var onClose: ((Bool) -> Void)?
    private var files: [ObjectIdentifier: URL] = [:]
    private var done = false
    private let spinner = UIActivityIndicatorView(style: .medium)

    init(start: URL, tint: String?) {
        self.start = start
        self.tint = SotdExportifyController.color(tint)
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { fatalError() }

    static func color(_ s: String?) -> UIColor? {
        guard var h = s?.trimmingCharacters(in: .whitespaces), h.hasPrefix("#"), h.count == 7 else { return nil }
        h.removeFirst()
        guard let v = UInt32(h, radix: 16) else { return nil }
        return UIColor(red: CGFloat((v >> 16) & 255) / 255, green: CGFloat((v >> 8) & 255) / 255, blue: CGFloat(v & 255) / 255, alpha: 1)
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        title = "Exportify"
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()   // (the Spotify sign-in kept for next time)
        cfg.preferences.javaScriptCanOpenWindowsAutomatically = true
        web = WKWebView(frame: view.bounds, configuration: cfg)
        web.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = true
        view.addSubview(web)
        navigationItem.leftBarButtonItem = UIBarButtonItem(title: "Close", style: .done, target: self, action: #selector(closeTapped))
        let safari = UIBarButtonItem(image: UIImage(systemName: "safari"), style: .plain, target: self, action: #selector(safariTapped))
        safari.accessibilityLabel = "Open in Safari"
        navigationItem.rightBarButtonItems = [safari, UIBarButtonItem(customView: spinner)]
        if let t = tint {
            let a = UINavigationBarAppearance()
            a.configureWithOpaqueBackground()
            a.backgroundColor = t
            a.titleTextAttributes = [.foregroundColor: UIColor.white]
            navigationController?.navigationBar.standardAppearance = a
            navigationController?.navigationBar.scrollEdgeAppearance = a
            navigationController?.navigationBar.tintColor = .white
        }
        web.load(URLRequest(url: start))
    }

    @objc func closeTapped() { finish(got: false) }
    @objc func safariTapped() { UIApplication.shared.open(web.url ?? start) }

    func finish(got: Bool) {
        if done { return }
        done = true
        let cb = onClose
        (navigationController ?? self).dismiss(animated: true) { cb?(got) }
    }

    // a download (Exportify's saveAs: a blob: link with download=, or a response marked as an attachment) comes here
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload { decisionHandler(.download); return }
        if let u = action.request.url, let sch = u.scheme?.lowercased(), !["https", "http", "blob", "data", "about"].contains(sch) {
            UIApplication.shared.open(u); decisionHandler(.cancel); return   // (spotify: and the like, to their app)
        }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let attach = ((response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? "").lowercased().contains("attachment")
        if attach || !response.canShowMIMEType { decisionHandler(.download); return }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self; spinner.startAnimating() }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self; spinner.startAnimating() }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("exportify-" + UUID().uuidString, isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        var name = suggestedFilename.isEmpty ? "playlist.csv" : suggestedFilename
        let ext = (name as NSString).pathExtension.lowercased()
        if ext != "zip" && ext != "csv" { name += (response.mimeType ?? "").contains("zip") ? ".zip" : ".csv" }
        let to = dir.appendingPathComponent(name)
        files[ObjectIdentifier(download)] = to
        completionHandler(to)
    }
    func downloadDidFinish(_ download: WKDownload) {
        spinner.stopAnimating()
        guard let url = files.removeValue(forKey: ObjectIdentifier(download)) else { return }
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        guard let data = try? Data(contentsOf: url), !data.isEmpty, data.count <= 80_000_000 else { warn("That file couldn’t be read. Try again."); return }
        onFile?(url.lastPathComponent, data)
        finish(got: true)
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        spinner.stopAnimating()
        if let url = files.removeValue(forKey: ObjectIdentifier(download)) { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        warn("The download didn’t finish. Try again.")
    }

    // a new window (the sign-in, or a link): in this same view
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if action.targetFrame == nil { webView.load(action.request) }
        return nil
    }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
        present(a, animated: true)
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
        a.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(true) })
        present(a, animated: true)
    }
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) { spinner.startAnimating() }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { spinner.stopAnimating() }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { spinner.stopAnimating() }

    private func warn(_ t: String) {
        let a = UIAlertController(title: nil, message: t, preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "OK", style: .default))
        present(a, animated: true)
    }
}
