import Foundation
import Capacitor
import AppIntents
import WidgetKit

// Siri and the Shortcuts app: "Hey Siri, play today's song in Song of the Day" (and shuffle, and My playlist). Each
// opens the app and hands the calendar what to play, as an "action" event of window.Capacitor.Plugins.SotdSiri (kept
// until the calendar listens, as it isn't loaded yet when Siri opens the app from closed).
// It also takes the member's sign-in for the widgets (member: { token, device }, or {} on signing out; kept in the
// shared keychain, SotdShared) and tells the widgets to look again (reload: { all }: every widget, or only "On your other
// device").
@objc(SotdSiriPlugin)
public class SotdSiriPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "SotdSiriPlugin"
    public let jsName = "SotdSiri"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "member", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "reload", returnType: CAPPluginReturnPromise)
    ]
    static weak var shared: SotdSiriPlugin?
    static var pending: String?

    override public func load() {
        SotdSiriPlugin.shared = self
        if let a = SotdSiriPlugin.pending { SotdSiriPlugin.pending = nil; send(a) }
    }

    @objc func member(_ call: CAPPluginCall) {
        let token = call.getString("token") ?? "", device = call.getString("device") ?? ""
        SotdShared.save(token.isEmpty ? nil : ["token": token, "device": device])
        WidgetCenter.shared.reloadTimelines(ofKind: "elsewhere")
        call.resolve()
    }

    @objc func reload(_ call: CAPPluginCall) {
        if call.getBool("all") ?? false { WidgetCenter.shared.reloadAllTimelines() } else { WidgetCenter.shared.reloadTimelines(ofKind: "elsewhere") }
        call.resolve()
    }

    func send(_ action: String) { notifyListeners("action", data: ["action": action], retainUntilConsumed: true) }

    static func run(_ action: String) {
        DispatchQueue.main.async {
            if let p = shared { p.send(action) } else { pending = action }
        }
    }
}

@available(iOS 16.0, *)
struct PlayTodayIntent: AppIntent {
    static let title: LocalizedStringResource = "Play today’s song"
    static let description: IntentDescription? = IntentDescription("Plays today’s Song of the Day, then the days before it.")
    static let openAppWhenRun: Bool = true
    func perform() async throws -> some IntentResult {
        SotdSiriPlugin.run("today")
        return .result()
    }
}

@available(iOS 16.0, *)
struct ShuffleSongsIntent: AppIntent {
    static let title: LocalizedStringResource = "Shuffle the songs"
    static let description: IntentDescription? = IntentDescription("Plays every Song of the Day, shuffled.")
    static let openAppWhenRun: Bool = true
    func perform() async throws -> some IntentResult {
        SotdSiriPlugin.run("shuffle")
        return .result()
    }
}

@available(iOS 16.0, *)
struct PlayMyPlaylistIntent: AppIntent {
    static let title: LocalizedStringResource = "Play my playlist"
    static let description: IntentDescription? = IntentDescription("Plays the songs you’ve added to My playlist.")
    static let openAppWhenRun: Bool = true
    func perform() async throws -> some IntentResult {
        SotdSiriPlugin.run("mine")
        return .result()
    }
}

@available(iOS 16.0, *)
struct SotdShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: PlayTodayIntent(), phrases: [
            "Play today's song in \(.applicationName)",
            "Play \(.applicationName)"
        ])
        AppShortcut(intent: ShuffleSongsIntent(), phrases: [
            "Shuffle \(.applicationName)",
            "Shuffle the songs in \(.applicationName)"
        ])
        AppShortcut(intent: PlayMyPlaylistIntent(), phrases: [
            "Play my playlist in \(.applicationName)"
        ])
    }
}
