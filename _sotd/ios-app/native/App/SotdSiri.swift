import Foundation
import Capacitor
import AppIntents

// Siri and the Shortcuts app: "Hey Siri, play today's song in Song of the Day" (and shuffle, and My playlist). Each
// opens the app and hands the calendar what to play, as an "action" event of window.Capacitor.Plugins.SotdSiri (kept
// until the calendar listens, as it isn't loaded yet when Siri opens the app from closed).
@objc(SotdSiriPlugin)
public class SotdSiriPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "SotdSiriPlugin"
    public let jsName = "SotdSiri"
    public let pluginMethods: [CAPPluginMethod] = []
    static weak var shared: SotdSiriPlugin?
    static var pending: String?

    override public func load() {
        SotdSiriPlugin.shared = self
        if let a = SotdSiriPlugin.pending { SotdSiriPlugin.pending = nil; send(a) }
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
