import Foundation
import Capacitor
import GoogleCast

// Google Cast (Chromecasts, Nest and Google Cast speakers) for the calendar, as window.Capacitor.Plugins.SotdCast.
// The phone looks for them once the calendar asks (start: the first time, the iPhone asks whether the app may use the
// local network), the calendar shows them in its own list with AirPlay, and the song plays on the one chosen: the
// speaker fetches it itself. The calendar keeps its own player going, muted, and has this follow it (load, play,
// pause, seek); the speaker's own state comes back as "status" events, its sessions as "session", the speakers found
// as "devices".
@objc(SotdCastPlugin)
public class SotdCastPlugin: CAPPlugin, CAPBridgedPlugin, GCKDiscoveryManagerListener, GCKSessionManagerListener, GCKRemoteMediaClientListener {
    public let identifier = "SotdCastPlugin"
    public let jsName = "SotdCast"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "start", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "connect", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "disconnect", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "load", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "play", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "pause", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "seek", returnType: CAPPluginReturnPromise)
    ]
    private var looking = false
    private var timer: Timer?

    // once, as the app starts (AppDelegate): Google's default receiver, discovery only when the calendar asks, and
    // sessions kept going with the app in the background (a song playing on with the phone locked)
    static func setUp() {
        let criteria = GCKDiscoveryCriteria(applicationID: kGCKDefaultMediaReceiverApplicationID)
        let options = GCKCastOptions(discoveryCriteria: criteria)
        options.disableDiscoveryAutostart = true
        options.suspendSessionsWhenBackgrounded = false
        options.physicalVolumeButtonsWillControlDeviceVolume = true
        GCKCastContext.setSharedInstanceWith(options)
    }

    override public func load() {
        let ctx = GCKCastContext.sharedInstance()
        ctx.discoveryManager.add(self)
        ctx.sessionManager.add(self)
    }

    private var client: GCKRemoteMediaClient? {
        return GCKCastContext.sharedInstance().sessionManager.currentSession?.remoteMediaClient
    }

    private func devices() -> [[String: Any]] {
        let dm = GCKCastContext.sharedInstance().discoveryManager
        var out: [[String: Any]] = []
        if dm.deviceCount > 0 {
            for i in 0..<dm.deviceCount {
                let d = dm.device(at: i)
                out.append(["id": d.deviceID, "name": d.friendlyName ?? d.modelName ?? "Speaker", "model": d.modelName ?? ""])
            }
        }
        return out
    }

    private func finite(_ x: TimeInterval) -> TimeInterval { return x.isFinite && x >= 0 ? x : 0 }

    private func sendStatus() {
        guard let c = client, let st = c.mediaStatus else { return }
        var state = "idle"
        switch st.playerState {
        case .playing: state = "playing"
        case .paused: state = "paused"
        case .buffering: state = "buffering"
        default: state = "idle"
        }
        let finished = st.playerState == .idle && st.idleReason == .finished
        notifyListeners("status", data: ["state": state, "reason": finished ? "finished" : "",
                                         "position": finite(c.approximateStreamPosition()),
                                         "duration": finite(st.mediaInformation?.streamDuration ?? 0)])
    }

    // ---- the calendar's calls ----
    @objc func start(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            if !self.looking { self.looking = true; GCKCastContext.sharedInstance().discoveryManager.startDiscovery() }
            let s = GCKCastContext.sharedInstance().sessionManager
            call.resolve(["devices": self.devices(), "connected": s.hasConnectedCastSession(),
                          "name": s.currentSession?.device.friendlyName ?? ""])
        }
    }

    @objc func connect(_ call: CAPPluginCall) {
        guard let id = call.getString("id") else { call.reject("No speaker given"); return }
        DispatchQueue.main.async {
            let dm = GCKCastContext.sharedInstance().discoveryManager
            var found: GCKDevice?
            if dm.deviceCount > 0 { for i in 0..<dm.deviceCount where dm.device(at: i).deviceID == id { found = dm.device(at: i) } }
            guard let d = found else { call.reject("That speaker isn’t on the Wi-Fi now"); return }
            if GCKCastContext.sharedInstance().sessionManager.startSession(with: d) { call.resolve() } else { call.reject("Couldn’t reach the speaker") }
        }
    }

    @objc func disconnect(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            _ = GCKCastContext.sharedInstance().sessionManager.endSessionAndStopCasting(true)
            call.resolve()
        }
    }

    @objc func load(_ call: CAPPluginCall) {
        guard let s = call.getString("url"), let url = URL(string: s) else { call.reject("No song given"); return }
        DispatchQueue.main.async {
            guard let c = self.client else { call.reject("Not playing on a speaker"); return }
            let md = GCKMediaMetadata(metadataType: .musicTrack)
            md.setString(call.getString("title") ?? "", forKey: kGCKMetadataKeyTitle)
            md.setString(call.getString("artist") ?? "", forKey: kGCKMetadataKeyArtist)
            md.setString("John’s Song of the Day", forKey: kGCKMetadataKeyAlbumTitle)
            if let i = call.getString("image"), let iu = URL(string: i) { md.addImage(GCKImage(url: iu, width: 640, height: 640)) }
            let b = GCKMediaInformationBuilder(contentURL: url)
            b.streamType = .buffered
            b.contentType = call.getString("type") ?? "audio/mpeg"
            b.metadata = md
            let o = GCKMediaLoadOptions()
            o.autoplay = call.getBool("play") ?? true
            o.playPosition = call.getDouble("position") ?? 0
            _ = c.loadMedia(b.build(), with: o)
            call.resolve()
        }
    }

    @objc func play(_ call: CAPPluginCall) { DispatchQueue.main.async { _ = self.client?.play(); call.resolve() } }
    @objc func pause(_ call: CAPPluginCall) { DispatchQueue.main.async { _ = self.client?.pause(); call.resolve() } }

    @objc func seek(_ call: CAPPluginCall) {
        let t = call.getDouble("t") ?? 0
        DispatchQueue.main.async {
            let o = GCKMediaSeekOptions()
            o.interval = t
            o.resumeState = .unchanged
            _ = self.client?.seek(with: o)
            call.resolve()
        }
    }

    // ---- Google's side ----
    public func didUpdateDeviceList() { notifyListeners("devices", data: ["devices": devices()]) }

    public func sessionManager(_ sessionManager: GCKSessionManager, willStart session: GCKSession) {
        notifyListeners("session", data: ["state": "connecting", "name": session.device.friendlyName ?? ""])
    }

    public func sessionManager(_ sessionManager: GCKSessionManager, didStart session: GCKSession) {
        session.remoteMediaClient?.add(self)
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.sendStatus() }
        notifyListeners("session", data: ["state": "connected", "name": session.device.friendlyName ?? ""])
    }

    public func sessionManager(_ sessionManager: GCKSessionManager, didEnd session: GCKSession, withError error: Error?) {
        timer?.invalidate(); timer = nil
        notifyListeners("session", data: ["state": "ended", "name": session.device.friendlyName ?? "", "error": error?.localizedDescription ?? ""])
    }

    public func sessionManager(_ sessionManager: GCKSessionManager, didFailToStart session: GCKSession, withError error: Error) {
        timer?.invalidate(); timer = nil
        notifyListeners("session", data: ["state": "failed", "name": session.device.friendlyName ?? "", "error": error.localizedDescription])
    }

    public func remoteMediaClient(_ client: GCKRemoteMediaClient, didUpdate mediaStatus: GCKMediaStatus?) { sendStatus() }
}
