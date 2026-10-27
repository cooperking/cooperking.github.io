import WidgetKit
import SwiftUI
import AppIntents

// The app's widgets (the data in SotdModel.swift, the looks in SotdViews.swift):
//  - Song of the Day (small, medium; and on the Lock Screen): the latest song. A tap plays all from it, on through the
//    days before; from iOS 17 its settings (hold it, Edit Widget) choose Play all, Shuffle, My playlist or that day.
//  - Calendar (large: this month's covers, a tap on a day plays all from it, arrows for other months from iOS 17;
//    medium: the last seven days).
//  - Play (medium): the latest song, with Play all, Shuffle and My playlist.
//  - On your other device (small, medium): what the member is playing elsewhere (the Worker's note); a tap carries it
//    on here.
//  - From iOS 18, a control for Control Centre, the Lock Screen and the Action button: Song of the Day, plays all from
//    the latest song.
// Each opens the app at the calendar's own address saying what to play (?do=today, shuffle, mine, carry; ?do=all#day).

extension WSize {
    init(_ f: WidgetFamily) {
        switch f {
        case .systemSmall: self = .small
        case .systemLarge, .systemExtraLarge: self = .large
        default:
            if #available(iOSApplicationExtension 16.0, *) {
                switch f {
                case .accessoryRectangular: self = .rect
                case .accessoryCircular: self = .circle
                case .accessoryInline: self = .inline
                default: self = .medium
                }
            } else {
                self = .medium
            }
        }
    }
    var accessory: Bool { return self == .rect || self == .circle || self == .inline }
}

extension View {
    // iOS 17's widget background (it asks for this), else a plain one with the margins it would have given; the Lock
    // Screen's widgets keep the system's
    @ViewBuilder func sotdBackground<B: View>(_ b: B, accessory: Bool = false) -> some View {
        if #available(iOSApplicationExtension 17.0, *) {
            if accessory {
                self.containerBackground(for: .widget) { Color.clear }
            } else {
                self.containerBackground(for: .widget) { b }
            }
        } else if accessory {
            self
        } else {
            self.padding(16).background(b)
        }
    }
}

// ---------- Song of the Day ----------
struct TodayEntryView: View {
    @Environment(\.widgetFamily) var family
    let entry: SongEntry
    var body: some View {
        let size = WSize(family)
        TodayView(entry: entry, size: size)
            .widgetURL(entry.tap)
            .sotdBackground(SotdBackground(ground: entry.ground, glow: entry.glow), accessory: size.accessory)
    }
}

enum TodayFamilies {
    static var list: [WidgetFamily] {
        if #available(iOSApplicationExtension 16.0, *) {
            return [.systemSmall, .systemMedium, .accessoryRectangular, .accessoryCircular, .accessoryInline]
        }
        return [.systemSmall, .systemMedium]
    }
}

struct TodayProvider: TimelineProvider {
    func placeholder(in context: Context) -> SongEntry { return .sample }
    func getSnapshot(in context: Context, completion: @escaping (SongEntry) -> Void) { Feed.fetch(tap: .playAll) { completion($0 ?? .sample) } }
    func getTimeline(in context: Context, completion: @escaping (Timeline<SongEntry>) -> Void) {
        Feed.fetch(tap: .playAll) { e in
            completion(Timeline(entries: [e ?? .sample], policy: .after(Date().addingTimeInterval(e == nil ? 20 * 60 : 45 * 60))))
        }
    }
}

// before iOS 17: no settings, a tap plays all
struct TodayWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "today", provider: TodayProvider()) { TodayEntryView(entry: $0) }
            .configurationDisplayName("Song of the Day")
            .description("The latest song: tap it to play all from it, on through the days before.")
            .supportedFamilies(TodayFamilies.list)
    }
}

@available(iOSApplicationExtension 16.0, *)
enum TapAction: String, AppEnum {
    case playAll, shuffle, mine, day
    static var typeDisplayRepresentation: TypeDisplayRepresentation = "Tap"
    static var caseDisplayRepresentations: [TapAction: DisplayRepresentation] = [
        .playAll: "Play all from the latest song", .shuffle: "Shuffle", .mine: "My playlist", .day: "Only the latest song"
    ]
    var choice: TapChoice { return TapChoice(rawValue: rawValue) ?? .playAll }
}

@available(iOSApplicationExtension 17.0, *)
struct TodaySettings: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Song of the Day"
    static var description = IntentDescription("Choose what a tap plays.")
    @Parameter(title: "A tap plays", default: .playAll) var tap: TapAction
}

@available(iOSApplicationExtension 17.0, *)
struct TodaySettingsProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> SongEntry { return .sample }
    func snapshot(for configuration: TodaySettings, in context: Context) async -> SongEntry { return await load(configuration.tap.choice) ?? .sample }
    func timeline(for configuration: TodaySettings, in context: Context) async -> Timeline<SongEntry> {
        let e = await load(configuration.tap.choice)
        return Timeline(entries: [e ?? .sample], policy: .after(Date().addingTimeInterval(e == nil ? 20 * 60 : 45 * 60)))
    }
    func load(_ tap: TapChoice) async -> SongEntry? {
        return await withCheckedContinuation { k in Feed.fetch(tap: tap) { k.resume(returning: $0) } }
    }
}

// from iOS 17: its settings choose what a tap plays (the same kind, so a widget already there keeps its place)
@available(iOSApplicationExtension 17.0, *)
struct TodaySettingsWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: "today", intent: TodaySettings.self, provider: TodaySettingsProvider()) { TodayEntryView(entry: $0) }
            .configurationDisplayName("Song of the Day")
            .description("The latest song. A tap plays all from it, on through the days before: hold it and choose Edit Widget for Shuffle, My playlist or only the song.")
            .supportedFamilies(TodayFamilies.list)
    }
}

// ---------- Play ----------
struct PlayEntryView: View {
    let entry: SongEntry
    var body: some View {
        PlayView(entry: entry)
            .widgetURL(SotdLinks.act("today"))
            .sotdBackground(SotdBackground(ground: entry.ground, glow: entry.glow))
    }
}

struct PlayWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "play", provider: TodayProvider()) { PlayEntryView(entry: $0) }
            .configurationDisplayName("Play")
            .description("The latest song, with Play all, Shuffle and My playlist.")
            .supportedFamilies([.systemMedium])
    }
}

// ---------- Calendar ----------
struct CalendarProvider: TimelineProvider {
    func placeholder(in context: Context) -> CalendarEntry { return .sample() }
    func getSnapshot(in context: Context, completion: @escaping (CalendarEntry) -> Void) { load(completion) }
    func getTimeline(in context: Context, completion: @escaping (Timeline<CalendarEntry>) -> Void) {
        load { e in
            // again in two hours, or just after midnight (a new day to mark), whichever comes first
            let c = CalendarModel.cal
            let midnight = c.date(byAdding: .day, value: 1, to: c.startOfDay(for: Date()))!.addingTimeInterval(120)
            completion(Timeline(entries: [e], policy: .after(min(midnight, Date().addingTimeInterval(2 * 3600)))))
        }
    }
    func load(_ done: @escaping (CalendarEntry) -> Void) {
        var off = 0   // (no arrows before iOS 17: this month)
        if #available(iOSApplicationExtension 17.0, *) { off = MonthPick.offset }
        Days.fetch { days, pals in
            Thumbs.fetch(CalendarModel.covers(days: days, offset: off)) {
                done(CalendarModel.entry(days: days, palettes: pals, offset: off))
            }
        }
    }
}

struct CalendarEntryView: View {
    @Environment(\.widgetFamily) var family
    let entry: CalendarEntry
    var body: some View {
        var arrows = false
        if #available(iOSApplicationExtension 17.0, *) { arrows = true }
        return CalendarView(entry: entry, size: WSize(family), arrows: arrows)
            .widgetURL(SotdLinks.calendar)
            .sotdBackground(SotdBackground(ground: entry.palette.ground, glow: entry.palette.glow))
    }
}

struct CalendarWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "calendar", provider: CalendarProvider()) { CalendarEntryView(entry: $0) }
            .configurationDisplayName("Calendar")
            .description("This month’s songs (or the last seven days): tap a day to play all from it, on through the days before.")
            .supportedFamilies([.systemLarge, .systemMedium])
    }
}

// ---------- On your other device ----------
struct ElseProvider: TimelineProvider {
    func placeholder(in context: Context) -> ElseEntry { return .sample }
    func getSnapshot(in context: Context, completion: @escaping (ElseEntry) -> Void) {
        if context.isPreview { completion(.sample); return }
        load(completion)
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<ElseEntry>) -> Void) {
        // again in 15 minutes (the app also tells it the moment another device's song changes, while the app is open)
        load { completion(Timeline(entries: [$0], policy: .after(Date().addingTimeInterval(15 * 60)))) }
    }
    func load(_ done: @escaping (ElseEntry) -> Void) {
        let m = SotdShared.load()
        Elsewhere.fetch(token: m?["token"], device: m?["device"], done)
    }
}

struct ElseEntryView: View {
    @Environment(\.widgetFamily) var family
    let entry: ElseEntry
    var body: some View {
        let v = ElseView(entry: entry, size: WSize(family))
        return v.widgetURL(v.tap)
            .sotdBackground(SotdBackground(ground: entry.palette.ground, glow: entry.palette.glow))
    }
}

struct ElsewhereWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "elsewhere", provider: ElseProvider()) { ElseEntryView(entry: $0) }
            .configurationDisplayName("On your other device")
            .description("What you’re playing on your other devices: tap to carry on here.")
            .supportedFamilies([.systemSmall, .systemMedium])
    }
}

// ---------- the control (Control Centre, the Lock Screen, the Action button) ----------
@available(iOSApplicationExtension 18.0, *)
struct PlaySongsIntent: AppIntent {
    static var title: LocalizedStringResource = "Play Song of the Day"
    static var description = IntentDescription("Plays the latest song, then the days before it.")
    func perform() async throws -> some IntentResult & OpensIntent {
        return .result(opensIntent: OpenURLIntent(SotdLinks.act("today")))
    }
}

@available(iOSApplicationExtension 18.0, *)
struct PlayControl: ControlWidget {
    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: "io.github.cooperking.sotd.play") {
            ControlWidgetButton(action: PlaySongsIntent()) {
                Label("Song of the Day", systemImage: "music.note")
            }
        }
        .displayName("Song of the Day")
        .description("Plays the latest song, then the days before it.")
    }
}

// ---------- all of them, as the iPhone's version allows ----------
@main
struct SotdWidgets: WidgetBundle {
    var body: some Widget { return widgets() }

    // (one availability check to each: an opaque result can differ only across a single if #available ... else)
    func widgets() -> some Widget {
        if #available(iOSApplicationExtension 18.0, *) {
            return widgets18
        } else {
            return before18()
        }
    }

    func before18() -> some Widget {
        if #available(iOSApplicationExtension 17.0, *) {
            return widgets17
        } else {
            return widgets15
        }
    }

    @WidgetBundleBuilder var widgets15: some Widget {
        TodayWidget()
        CalendarWidget()
        PlayWidget()
        ElsewhereWidget()
    }

    @available(iOSApplicationExtension 17.0, *)
    @WidgetBundleBuilder var widgets17: some Widget {
        TodaySettingsWidget()
        CalendarWidget()
        PlayWidget()
        ElsewhereWidget()
    }

    @available(iOSApplicationExtension 18.0, *)
    @WidgetBundleBuilder var widgets18: some Widget {
        TodaySettingsWidget()
        CalendarWidget()
        PlayWidget()
        ElsewhereWidget()
        PlayControl()
    }
}
