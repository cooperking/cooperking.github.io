import UIKit
import WebKit

// Never two of a member's devices playing at once, with the app away (John, 29 Oct 2026: "Playlist just started playing
// on two devices at once", the iPhone app playing in the background). Away (in the background, or the phone locked), a
// song carries on here, but iOS holds back the page itself: it doesn't hear that another of the member's devices has
// begun playing, and plays on. So while the app is away and a song is playing here, the app asks the Worker itself,
// every 10 seconds (GET /members/now, with the member's sign-in the calendar handed over: SotdShared), and if another
// device has begun playing since the app went away, it pauses the song here and tells the page (window.sotdTakenBy, which
// shows "Playing on your Mac now" and the other's song in My playlist's box, as when it's told by the Worker). Back in
// sight, the page looks for itself again. From build 25.
final class SotdGuard {
    static let shared = SotdGuard()
    private weak var webView: WKWebView?
    private var timer: Timer?
    private var since: Double?   // the Worker's time when the app went away
    private var asking = false

    func attach(_ webView: WKWebView?) {
        self.webView = webView
        let nc = NotificationCenter.default
        nc.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { [weak self] _ in self?.away() }
        nc.addObserver(forName: UIApplication.willEnterForegroundNotification, object: nil, queue: .main) { [weak self] _ in self?.back() }
    }

    private func away() {
        timer?.invalidate()
        since = nil
        guard let token = SotdShared.load()?["token"], !token.isEmpty else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in self?.look() }
        look()   // (the first: the Worker's time now)
    }

    private func back() {
        timer?.invalidate()
        timer = nil
        since = nil
    }

    private func look() {
        guard !asking, let web = webView, let m = SotdShared.load(), let token = m["token"], !token.isEmpty, let me = m["device"], !me.isEmpty else { return }
        web.requestMediaPlaybackState { [weak self] state in
            guard let self = self else { return }
            if state != .playing && self.since != nil { return }   // (nothing playing here: nothing to stop)
            self.ask(token) { now, rec in
                guard let now = now else { return }
                guard let since = self.since else { self.since = now; return }
                guard state == .playing, let rec = rec, let dev = rec["dev"] as? String, dev != me,
                      (rec["playing"] as? Bool) == true, let at = rec["at"] as? Double, at > since else { return }
                let label = (rec["label"] as? String) ?? "other device"
                web.pauseAllMediaPlayback(completionHandler: nil)
                let said = (try? JSONSerialization.data(withJSONObject: [label])).flatMap { String(data: $0, encoding: .utf8) } ?? "[\"other device\"]"
                web.evaluateJavaScript("window.sotdTakenBy && window.sotdTakenBy(" + said + "[0])", completionHandler: nil)
            }
        }
    }

    private func ask(_ token: String, _ done: @escaping (Double?, [String: Any]?) -> Void) {
        guard let url = URL(string: "https://sotd-playlists.cooperking.workers.dev/members/now") else { return }
        var rq = URLRequest(url: url)
        rq.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        rq.cachePolicy = .reloadIgnoringLocalCacheData
        rq.timeoutInterval = 8
        asking = true
        URLSession.shared.dataTask(with: rq) { data, _, _ in
            let j = data.flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] }
            DispatchQueue.main.async {
                self.asking = false
                done(j?["now"] as? Double, j?["rec"] as? [String: Any])
            }
        }.resume()
    }
}
