import UIKit
import AVFoundation
import Capacitor
import SpotifyiOS

// Spotify on this iPhone, woken through Spotify's own iPhone toolkit (John, 2 Nov 2026: songs through a member's Spotify
// on the phone itself; Spotify asleep in the background didn't take songs sent by the Web API). wake({ clientId, uri })
// makes Spotify on this phone play that song: straight away when the toolkit is connected; otherwise through Spotify's
// own sign-in (authorizeAndPlayURI: a moment in Spotify, then back here at sotdspotify://callback). Then the calendar sends
// the song and the ones after it through the Web API to this phone, which is awake now. available() says whether Spotify
// is on the phone. Each of the Song of the Day Spotify apps needs this app's ID (io.github.cooperking.sotd) and the
// Redirect URI sotdspotify://callback in Spotify's dashboard. From build 32; build 35 works the phone's player itself; build 38 gives up on a hanging connection after 4 seconds; build 39 keeps Spotify's latest state as it sends it and names each failure; build 40 wakes it properly (force) when its requests fail (state, enqueue, pause, resume, seek, skip); from build 34 the app's own sound shares with Spotify's (share()), so coming back here doesn't stop it.
@objc(SotdSpotifyPlugin)
public class SotdSpotifyPlugin: CAPPlugin, CAPBridgedPlugin, SPTAppRemoteDelegate, SPTAppRemotePlayerStateDelegate {
    public let identifier = "SotdSpotifyPlugin"
    public let jsName = "SotdSpotify"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "available", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "wake", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "share", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "state", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "enqueue", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "pause", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "resume", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "seek", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "skip", returnType: CAPPluginReturnPromise)
    ]
    static let redirect = URL(string: "sotdspotify://callback")!
    private var remote: SPTAppRemote?
    private var clientId = ""
    private var pendingUri = ""
    private var pending: CAPPluginCall?
    private var authorizing = false
    private var waiting: [(SPTAppRemotePlayerAPI?) -> Void] = []   // (calls made while it connects again)
    private var last: [String: Any] = [:]   // (Spotify's latest state, as it sends it: used when asking for it fails; build 39)
    private var lastAt = Date.distantPast
    private var lastErr = ""
    private func stateDict(_ st: SPTAppRemotePlayerState) -> [String: Any] {
        let uri = st.track.uri
        return ["track": uri.hasPrefix("spotify:track:") ? String(uri.dropFirst(14)) : "", "pos": st.playbackPosition, "dur": Int(st.track.duration), "playing": !st.isPaused]
    }
    public func playerStateDidChange(_ playerState: SPTAppRemotePlayerState) {
        last = stateDict(playerState); lastAt = Date()
    }

    // the player itself (build 35 on: John, 2 Nov 2026: songs sent through the Web API after waking still didn't play on
    // the phone, so the phone's own Spotify is worked from here): its state, the next songs queued, pause, resume, seek,
    // skip; reconnected first when the connection has dropped (it does while the app's in the background)
    private func withApi(_ call: CAPPluginCall, _ run: @escaping (SPTAppRemotePlayerAPI) -> Void) {
        DispatchQueue.main.async {
            guard let r = self.remote else { call.reject("noRemote: not woken yet"); return }
            if r.isConnected, let api = r.playerAPI { run(api); return }
            guard r.connectionParameters.accessToken != nil else { call.reject("noToken: not connected and no sign-in kept"); return }
            self.waiting.append({ api in if let a = api { run(a) } else { call.reject("notConnected: " + (self.lastErr.isEmpty ? "connecting again failed" : self.lastErr)) } })
            if self.waiting.count == 1 {
                r.connect()
                DispatchQueue.main.asyncAfter(deadline: .now() + 4) { if !r.isConnected && !self.waiting.isEmpty { self.flushWaiting(false) } }   // (a connection that hangs: given up on, not left waiting)
            }
        }
    }
    private func flushWaiting(_ ok: Bool) {
        let w = waiting; waiting = []
        for f in w { f(ok ? remote?.playerAPI : nil) }
    }
    private func done(_ call: CAPPluginCall) -> SPTAppRemoteCallback { return { _, err in if let e = err { call.reject(e.localizedDescription) } else { call.resolve() } } }
    @objc func state(_ call: CAPPluginCall) {
        withApi(call) { api in
            api.getPlayerState { res, err in
                if err == nil, let st = res as? SPTAppRemotePlayerState { let d = self.stateDict(st); self.last = d; self.lastAt = Date(); call.resolve(d); return }
                if !self.last.isEmpty && Date().timeIntervalSince(self.lastAt) < 30 { var d = self.last; d["kept"] = true; call.resolve(d); return }   // (the latest it sent)
                call.reject(err.map { "stateError: " + $0.localizedDescription } ?? "stateError: Spotify sent no state (\(type(of: res)))")
            }
        }
    }
    @objc func enqueue(_ call: CAPPluginCall) {
        let uris = (call.getArray("uris", String.self) ?? []).filter { $0.hasPrefix("spotify:track:") }
        withApi(call) { api in
            var left = uris
            func next() {
                guard !left.isEmpty else { call.resolve(); return }
                let u = left.removeFirst()
                api.enqueueTrackUri(u) { _, _ in next() }
            }
            next()
        }
    }
    @objc func pause(_ call: CAPPluginCall) { withApi(call) { api in api.pause(self.done(call)) } }
    @objc func resume(_ call: CAPPluginCall) { withApi(call) { api in api.resume(self.done(call)) } }
    @objc func skip(_ call: CAPPluginCall) { withApi(call) { api in api.skip(toNext: self.done(call)) } }
    @objc func seek(_ call: CAPPluginCall) { let ms = call.getInt("ms") ?? 0; withApi(call) { api in api.seek(toPosition: ms, callback: self.done(call)) } }

    override public func load() {
        NotificationCenter.default.addObserver(self, selector: #selector(opened(_:)), name: .capacitorOpenURL, object: nil)
    }

    // the app's own sound shares with Spotify's (John, 2 Nov 2026: Spotify woke, then went quiet as he came back: this app's
    // audio, set up to take over the phone's sound for songs with the screen locked, took it back from Spotify). on: mixes
    // with others and lets go of the sound; off: back to taking it over, for its own songs
    static func setShare(_ on: Bool) {
        let s = AVAudioSession.sharedInstance()
        if on {
            try? s.setCategory(.playback, mode: .default, options: [.mixWithOthers])
            try? s.setActive(true)   // (active but mixing: the app's silent copy plays without stopping Spotify; build 41)
        } else {
            try? s.setCategory(.playback, mode: .default, options: [])
            try? s.setActive(true)
        }
    }
    @objc func share(_ call: CAPPluginCall) {
        let on = call.getBool("on") ?? true
        DispatchQueue.main.async { SotdSpotifyPlugin.setShare(on); call.resolve() }
    }

    @objc func available(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            let ok = UIApplication.shared.canOpenURL(URL(string: "spotify:")!)
            call.resolve(["installed": ok])
        }
    }

    private func remoteFor(_ id: String) -> SPTAppRemote {
        if let r = remote, clientId == id { return r }
        remote?.disconnect()
        clientId = id
        let conf = SPTConfiguration(clientID: id, redirectURL: SotdSpotifyPlugin.redirect)
        let r = SPTAppRemote(configuration: conf, logLevel: .error)
        r.delegate = self
        remote = r
        return r
    }

    @objc func wake(_ call: CAPPluginCall) {
        guard let id = call.getString("clientId"), id.count == 32, let uri = call.getString("uri"), uri.hasPrefix("spotify:track:") else { call.reject("clientId and uri needed"); return }
        DispatchQueue.main.async {
            SotdSpotifyPlugin.setShare(true)   // (so coming back here doesn't stop Spotify)
            if let old = self.pending { old.reject("superseded") }
            self.pending = call
            self.pendingUri = uri
            let r = self.remoteFor(id)
            // force (build 40): straight to Spotify's own sign-in, which brings Spotify forward and plays the song itself (a
            // connection to a Spotify asleep in the background connects, but its requests fail: "The request failed")
            if call.getBool("force") ?? false { r.connectionParameters.accessToken = nil; if r.isConnected { r.disconnect() }; self.authorize(); return }
            if r.isConnected { self.playNow(); return }
            if r.connectionParameters.accessToken != nil {   // (a token from before: tried first, for 4 seconds; then Spotify's own sign-in, which wakes it)
                self.authorizing = false; r.connect()
                DispatchQueue.main.asyncAfter(deadline: .now() + 4) { if self.pending === call && !r.isConnected && !self.authorizing { r.connectionParameters.accessToken = nil; self.authorize() } }
                return
            }
            self.authorize()
        }
    }

    private func authorize() {
        guard let r = remote else { return }
        authorizing = true
        r.authorizeAndPlayURI(pendingUri) { installed in
            if !installed { self.finish(error: "notInstalled") }
        }
    }

    private func playNow() {
        guard let api = remote?.playerAPI else { finish(error: "notConnected"); return }
        api.play(pendingUri) { _, err in
            if let e = err { self.finish(error: e.localizedDescription) } else { self.finish(error: nil) }
        }
    }

    private func finish(error: String?) {
        DispatchQueue.main.async {
            guard let c = self.pending else { return }
            self.pending = nil
            if let e = error { c.reject(e) } else { c.resolve(["ok": true]) }
        }
    }

    // back from Spotify's sign-in: its token, then the connection
    @objc func opened(_ note: Notification) {
        let info = (note.object as? [String: Any]) ?? (note.userInfo as? [String: Any]) ?? [:]
        guard let url = info["url"] as? URL, url.scheme == "sotdspotify" else { return }
        DispatchQueue.main.async {
            guard let r = self.remote else { return }
            let params = r.authorizationParameters(from: url)
            if let token = params?[SPTAppRemoteAccessTokenKey] {
                r.connectionParameters.accessToken = token
                r.connect()
            } else {
                self.finish(error: params?[SPTAppRemoteErrorDescriptionKey] ?? "notAuthorized")
            }
        }
    }

    public func appRemoteDidEstablishConnection(_ appRemote: SPTAppRemote) {
        SotdSpotifyPlugin.setShare(true)
        lastErr = ""
        appRemote.playerAPI?.delegate = self
        appRemote.playerAPI?.subscribe(toPlayerState: { _, _ in })
        flushWaiting(true)
        if authorizing { authorizing = false; finish(error: nil) }   // (Spotify's already playing the song it was asked for)
        else if pending != nil { playNow() }
    }

    public func appRemote(_ appRemote: SPTAppRemote, didFailConnectionAttemptWithError error: Error?) {
        lastErr = "connect failed: " + (error?.localizedDescription ?? "no reason")
        flushWaiting(false)
        if !authorizing && pending != nil { appRemote.connectionParameters.accessToken = nil; authorize(); return }   // (the token from before has run out: Spotify's sign-in again)
        authorizing = false
        finish(error: error?.localizedDescription ?? "notConnected")
    }

    public func appRemote(_ appRemote: SPTAppRemote, didDisconnectWithError error: Error?) { lastErr = "disconnected: " + (error?.localizedDescription ?? "no reason") }
}
