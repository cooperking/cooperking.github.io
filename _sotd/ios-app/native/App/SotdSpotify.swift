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
// Redirect URI sotdspotify://callback in Spotify's dashboard. From build 32; from build 34 the app's own sound shares with Spotify's (share()), so coming back here doesn't stop it.
@objc(SotdSpotifyPlugin)
public class SotdSpotifyPlugin: CAPPlugin, CAPBridgedPlugin, SPTAppRemoteDelegate {
    public let identifier = "SotdSpotifyPlugin"
    public let jsName = "SotdSpotify"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "available", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "wake", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "share", returnType: CAPPluginReturnPromise)
    ]
    static let redirect = URL(string: "sotdspotify://callback")!
    private var remote: SPTAppRemote?
    private var clientId = ""
    private var pendingUri = ""
    private var pending: CAPPluginCall?
    private var authorizing = false

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
            try? s.setActive(false, options: [.notifyOthersOnDeactivation])
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
            if r.isConnected { self.playNow(); return }
            if r.connectionParameters.accessToken != nil { self.authorizing = false; r.connect(); return }   // (a token from before: tried first)
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
        if authorizing { authorizing = false; finish(error: nil) }   // (Spotify's already playing the song it was asked for)
        else if pending != nil { playNow() }
    }

    public func appRemote(_ appRemote: SPTAppRemote, didFailConnectionAttemptWithError error: Error?) {
        if !authorizing && pending != nil { appRemote.connectionParameters.accessToken = nil; authorize(); return }   // (the token from before has run out: Spotify's sign-in again)
        authorizing = false
        finish(error: error?.localizedDescription ?? "notConnected")
    }

    public func appRemote(_ appRemote: SPTAppRemote, didDisconnectWithError error: Error?) {}
}
