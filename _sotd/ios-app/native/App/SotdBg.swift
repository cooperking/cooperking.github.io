import UIKit
import AVFoundation
import MediaPlayer
import Capacitor

// The app's own player for the hand-over from Spotify (John, 2 Nov 2026: "Go with silent copy handover", and "It should
// have the entire playlist"). While Spotify plays a song on this phone, the page gives this player the whole list from
// that song on (each song's MP3, or its preview) with load(); it plays the song at volume 0, sharing the phone's sound so
// Spotify isn't stopped, kept in step by sync(). When the app goes to the background, iPhone tells this code directly (no
// page needed): the song is turned up (a preview from its start), the phone's sound taken back (Spotify stops as it's
// taken), and it plays on through the list, with the lock screen's controls. Back in the app, the page asks status(), shows
// the song, and when that song ends ("ended" event) sends the next one to Spotify again. stop() ends it. From build 41.
@objc(SotdBgPlugin)
public class SotdBgPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "SotdBgPlugin"
    public let jsName = "SotdBg"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "load", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "sync", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stop", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "status", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "play", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "pause", returnType: CAPPluginReturnPromise)
    ]
    struct Item { let track: String; let url: URL; let full: Bool; let title: String; let artist: String }
    private var list: [Item] = []
    private var index = 0
    private var player: AVPlayer?
    private var armed = false     // (a song loaded beside Spotify: ready to take over)
    private var took = false      // (taken over: playing out loud)
    private var endObs: NSObjectProtocol?
    private var remoteOn = false
    private var log: [String] = []

    override public func load() {
        NotificationCenter.default.addObserver(self, selector: #selector(toBackground), name: UIApplication.didEnterBackgroundNotification, object: nil)
    }

    private func note(_ m: String) {
        let f = DateFormatter(); f.dateFormat = "HH:mm:ss"
        log.append(f.string(from: Date()) + " native: " + m); if log.count > 30 { log.removeFirst() }
    }

    @objc func load(_ call: CAPPluginCall) {
        let raw = call.getArray("list", JSObject.self) ?? []
        let items: [Item] = raw.compactMap { o in
            guard let t = o["track"] as? String, let u = o["url"] as? String, let url = URL(string: u) else { return nil }
            return Item(track: t, url: url, full: (o["full"] as? Bool) ?? false, title: (o["title"] as? String) ?? "", artist: (o["artist"] as? String) ?? "")
        }
        let at = call.getDouble("at") ?? 0, playing = call.getBool("playing") ?? true
        DispatchQueue.main.async {
            guard !items.isEmpty else { call.reject("empty list"); return }
            self.list = items; self.took = false; self.armed = true
            SotdSpotifyPlugin.setShare(true)
            self.playItem(0, at: at, volume: 0, play: playing)
            self.note("loaded \(items.count) songs, silent beside Spotify")
            call.resolve(["count": items.count])
        }
    }

    private func playItem(_ i: Int, at: Double, volume: Float, play: Bool) {
        guard i >= 0, i < list.count else { stopAll(); return }
        index = i
        let item = AVPlayerItem(url: list[i].url)
        if let o = endObs { NotificationCenter.default.removeObserver(o) }
        endObs = NotificationCenter.default.addObserver(forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main) { [weak self] _ in self?.itemEnded() }
        let p = player ?? AVPlayer()
        player = p
        p.replaceCurrentItem(with: item)
        p.volume = volume
        if at > 0.5 && list[i].full { p.seek(to: CMTime(seconds: at, preferredTimescale: 600)) }
        if play { p.play() }
        if took { nowPlaying() }
    }

    private func itemEnded() {
        if !took {   // (silent beside Spotify: a preview loops; an MP3 waits for the page's next song)
            if index < list.count, !list[index].full { player?.seek(to: .zero); player?.play() }
            return
        }
        if UIApplication.shared.applicationState == .active {   // (back in the app: this song over, the page sends the next to Spotify)
            let next = index + 1 < list.count ? list[index + 1].track : ""
            note("song over in the app: next to Spotify (\(next))")
            notifyListeners("ended", data: ["track": list[index].track, "next": next])
            stopAll()
            return
        }
        note("song over: the next")
        playItem(index + 1, at: 0, volume: 1, play: true)
    }

    // the app gone to the background: the song out loud, the phone's sound taken back
    @objc func toBackground() {
        guard armed, !took, let p = player, p.currentItem != nil, index < list.count else { return }
        took = true
        let s = AVAudioSession.sharedInstance()
        try? s.setCategory(.playback, mode: .default, options: [])
        try? s.setActive(true)
        if !list[index].full { p.seek(to: .zero) }
        p.volume = 1
        p.play()
        remoteControls(true)
        nowPlaying()
        note("took over in the background: \(list[index].title) (\(list[index].full ? "MP3 from \(Int(p.currentTime().seconds))s" : "preview"))")
    }

    private func nowPlaying() {
        guard index < list.count, let p = player else { return }
        var info: [String: Any] = [MPMediaItemPropertyTitle: list[index].title, MPMediaItemPropertyArtist: list[index].artist,
                                   MPNowPlayingInfoPropertyElapsedPlaybackTime: p.currentTime().seconds.isFinite ? p.currentTime().seconds : 0, MPNowPlayingInfoPropertyPlaybackRate: p.rate]
        if let d = p.currentItem?.asset.duration.seconds, d.isFinite { info[MPMediaItemPropertyPlaybackDuration] = d }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func remoteControls(_ on: Bool) {
        let c = MPRemoteCommandCenter.shared()
        if on && !remoteOn {
            remoteOn = true
            c.playCommand.addTarget { [weak self] _ in self?.player?.play(); self?.nowPlaying(); return .success }
            c.pauseCommand.addTarget { [weak self] _ in self?.player?.pause(); self?.nowPlaying(); return .success }
            c.nextTrackCommand.addTarget { [weak self] _ in guard let s = self else { return .commandFailed }; s.playItem(s.index + 1, at: 0, volume: 1, play: true); return .success }
            c.previousTrackCommand.addTarget { [weak self] _ in guard let s = self else { return .commandFailed }; s.playItem(max(0, s.index - 1), at: 0, volume: 1, play: true); return .success }
        } else if !on && remoteOn {
            remoteOn = false
            c.playCommand.removeTarget(nil); c.pauseCommand.removeTarget(nil); c.nextTrackCommand.removeTarget(nil); c.previousTrackCommand.removeTarget(nil)
        }
    }

    private func stopAll() {
        armed = false; took = false
        player?.pause(); player?.replaceCurrentItem(with: nil)
        if let o = endObs { NotificationCenter.default.removeObserver(o); endObs = nil }
        remoteControls(false)
    }

    @objc func sync(_ call: CAPPluginCall) {   // the silent copy kept to Spotify's place (and paused with it)
        let at = call.getDouble("at") ?? 0, playing = call.getBool("playing") ?? true
        DispatchQueue.main.async {
            guard self.armed, !self.took, let p = self.player, self.index < self.list.count else { call.resolve(); return }
            if playing && p.rate == 0 { p.play() }
            if !playing && p.rate != 0 { p.pause() }
            if self.list[self.index].full && abs(p.currentTime().seconds - at) > 1.5 { p.seek(to: CMTime(seconds: at, preferredTimescale: 600)) }
            call.resolve()
        }
    }

    @objc func stop(_ call: CAPPluginCall) { DispatchQueue.main.async { self.stopAll(); call.resolve() } }

    @objc func play(_ call: CAPPluginCall) { DispatchQueue.main.async { self.player?.play(); self.nowPlaying(); call.resolve() } }
    @objc func pause(_ call: CAPPluginCall) { DispatchQueue.main.async { self.player?.pause(); self.nowPlaying(); call.resolve() } }

    @objc func status(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            let p = self.player
            let t = self.index < self.list.count ? self.list[self.index].track : ""
            let at = p?.currentTime().seconds ?? 0
            call.resolve(["took": self.took, "armed": self.armed, "track": t, "at": at.isFinite ? at : 0,
                          "playing": (p?.rate ?? 0) != 0, "volume": p?.volume ?? 0, "log": self.log])
            self.log = []
        }
    }
}
