import SwiftUI
import WidgetKit
import AppIntents

// How the widgets look: the calendar's own colours (the month's ground with its glow top right), its serif titles and
// small spaced capitals. Each view is drawn for a size (WSize); the widgets (SotdWidget.swift) give it their family.

// a tap's address (a Link; drawn plainly for the pictures of the widgets, which can't draw a Link)
struct Tap<Content: View>: View {
    static var plain: Bool { return SotdLinks.plain }
    let url: URL
    @ViewBuilder let content: () -> Content
    var body: some View {
        if Tap.plain { content() } else { Link(destination: url) { content() } }
    }
}

struct SotdBackground: View {
    let ground: Color
    let glow: Color
    var body: some View {
        ZStack {
            ground
            RadialGradient(gradient: Gradient(colors: [glow.opacity(0.85), glow.opacity(0)]), center: .topTrailing, startRadius: 0, endRadius: 240)
        }
    }
}

struct Art: View {
    let cover: PlatformImage?
    var radius: CGFloat = 10
    var body: some View {
        if let c = cover {
            Image(platform: c).resizable().aspectRatio(contentMode: .fill)
                .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
        } else {
            RoundedRectangle(cornerRadius: radius, style: .continuous).fill(Color.white.opacity(0.18))
                .overlay(Image(systemName: "music.note").foregroundColor(.white.opacity(0.85)))
        }
    }
}

extension View {   // the Lock Screen's accent colour on it (iOS 16 on)
    @ViewBuilder func accent() -> some View {
        if #available(iOS 16.0, macOS 13.0, *) { self.widgetAccentable() } else { self }
    }
}

struct Caps: View {   // a small line in spaced capitals
    let text: String
    var size: CGFloat = 9
    var opacity: Double = 0.75
    var body: some View {
        Text(text.uppercased()).font(.system(size: size, weight: .semibold)).kerning(size * 0.11)
            .foregroundColor(Color.white.opacity(opacity)).lineLimit(1)
    }
}

// ---------- today's song ----------
struct TodayView: View {
    let entry: SongEntry
    let size: WSize

    var body: some View {
        switch size {
        case .small:
            VStack(alignment: .leading, spacing: 4) {
                Art(cover: entry.cover).frame(width: 62, height: 62)
                Spacer(minLength: 0)
                Text(entry.title).font(.system(size: 15, design: .serif)).foregroundColor(.white).lineLimit(2)
                Caps(text: entry.artist, size: 9, opacity: 0.8)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        case .rect:
            VStack(alignment: .leading, spacing: 1) {
                Text("SONG OF THE DAY").font(.system(size: 10, weight: .semibold)).kerning(0.8).accent()
                Text(entry.title).font(.system(size: 15, weight: .semibold, design: .serif)).lineLimit(1)
                Text(entry.artist).font(.system(size: 12)).lineLimit(1).opacity(0.8)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        case .circle:
            ZStack {
                if let c = entry.cover {
                    Image(platform: c).resizable().aspectRatio(contentMode: .fill).clipShape(Circle())
                } else {
                    Image(systemName: "music.note").font(.system(size: 22, weight: .semibold))
                }
            }
        case .inline:
            Label { Text(entry.title + " · " + entry.artist) } icon: { Image(systemName: "music.note") }
        default:
            HStack(spacing: 14) {
                Art(cover: entry.cover).frame(width: 112, height: 112)
                VStack(alignment: .leading, spacing: 4) {
                    Caps(text: entry.day, size: 10)
                    Text(entry.title).font(.system(size: 20, design: .serif)).foregroundColor(.white).lineLimit(2)
                    Caps(text: entry.artist, size: 11, opacity: 0.85)
                    Spacer(minLength: 0)
                    Caps(text: "John’s Song of the Day", size: 9, opacity: 0.6)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            }
        }
    }
}

// ---------- today's song with Play all, Shuffle and My playlist ----------
struct PlayView: View {
    let entry: SongEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                Art(cover: entry.cover, radius: 8).frame(width: 64, height: 64)
                VStack(alignment: .leading, spacing: 3) {
                    Caps(text: entry.day, size: 9)
                    Text(entry.title).font(.system(size: 18, design: .serif)).foregroundColor(.white).lineLimit(1)
                    Caps(text: entry.artist, size: 10, opacity: 0.85)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack(spacing: 8) {
                pill("Play all", "play.fill", SotdLinks.act("today"))
                pill("Shuffle", "shuffle", SotdLinks.act("shuffle"))
                pill("My playlist", "heart.fill", SotdLinks.act("mine"))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    func pill(_ label: String, _ icon: String, _ url: URL) -> some View {
        Tap(url: url) {
            HStack(spacing: 5) {
                Image(systemName: icon).font(.system(size: 10, weight: .semibold))
                Text(label).font(.system(size: 11, weight: .semibold)).lineLimit(1).minimumScaleFactor(0.8)
            }
            .foregroundColor(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 9)
            .background(Capsule().fill(Color.white.opacity(0.2)))
        }
    }
}

// ---------- the calendar: this month (large), the last seven days (medium) ----------
struct CalendarView: View {
    let entry: CalendarEntry
    let size: WSize
    let arrows: Bool   // the month arrows (iOS 17 on)

    var body: some View {
        if size == .large { month } else { week }
    }

    var month: some View {
        VStack(spacing: 6) {
            HStack(alignment: .center, spacing: 6) {
                (Text(entry.month + " ").font(.system(size: 22, design: .serif)) + Text(entry.year).font(.system(size: 22, design: .serif).italic()))
                    .foregroundColor(.white).lineLimit(1)
                Spacer(minLength: 4)
                if arrows { nav }
            }
            HStack(spacing: 4) {
                ForEach(Array(["M", "T", "W", "T", "F", "S", "S"].enumerated()), id: \.offset) { _, d in
                    Text(d).font(.system(size: 9, weight: .semibold)).foregroundColor(.white.opacity(0.65)).frame(maxWidth: .infinity)
                }
            }
            VStack(spacing: 4) {
                ForEach(0..<max(1, entry.cells.count / 7), id: \.self) { r in
                    HStack(spacing: 4) {
                        ForEach(0..<7, id: \.self) { c in
                            cell(r * 7 + c < entry.cells.count ? entry.cells[r * 7 + c] : nil)
                        }
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }

    @ViewBuilder var nav: some View {
        if #available(iOS 17.0, macOS 14.0, *) {
            HStack(spacing: 6) {
                if entry.offset > 0 {
                    Button(intent: MonthIntent(step: 0)) { navLabel(Image(systemName: "calendar")) }.buttonStyle(.plain)
                }
                Button(intent: MonthIntent(step: -1)) { navLabel(Image(systemName: "chevron.left")) }.buttonStyle(.plain)
                    .disabled(!entry.back).opacity(entry.back ? 1 : 0.35)
                Button(intent: MonthIntent(step: 1)) { navLabel(Image(systemName: "chevron.right")) }.buttonStyle(.plain)
                    .disabled(!entry.forward).opacity(entry.forward ? 1 : 0.35)
            }
        }
    }

    func navLabel(_ i: Image) -> some View {
        i.font(.system(size: 11, weight: .bold)).foregroundColor(.white).frame(width: 26, height: 26).background(Circle().fill(Color.white.opacity(0.2)))
    }

    @ViewBuilder func cell(_ c: DayCell?) -> some View {
        if let c = c, c.song {
            Tap(url: SotdLinks.playFrom(c.slug)) {
                Color.clear.aspectRatio(1, contentMode: .fit)
                    .overlay(Art(cover: c.cover, radius: 6))
                    .overlay(alignment: .topLeading) {
                        Text("\(c.day)").font(.system(size: 8, weight: .bold)).foregroundColor(.white)
                            .padding(.horizontal, 3).padding(.vertical, 1)
                            .background(RoundedRectangle(cornerRadius: 3).fill(Color.black.opacity(0.5))).padding(2)
                    }
                    .overlay(RoundedRectangle(cornerRadius: 7).stroke(Color.white, lineWidth: c.today ? 2 : 0).padding(-1))
            }
            .frame(maxWidth: .infinity)
        } else if let c = c {
            Color.clear.aspectRatio(1, contentMode: .fit)
                .overlay(RoundedRectangle(cornerRadius: 6).fill(Color.white.opacity(0.08)))
                .overlay(Text("\(c.day)").font(.system(size: 9, weight: .medium)).foregroundColor(.white.opacity(0.45)))
                .overlay(RoundedRectangle(cornerRadius: 7).stroke(Color.white, lineWidth: c.today ? 2 : 0).padding(-1))
                .frame(maxWidth: .infinity)
        } else {
            Color.clear.aspectRatio(1, contentMode: .fit).frame(maxWidth: .infinity)
        }
    }

    var week: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Caps(text: "Latest days", size: 10)
                Spacer(minLength: 4)
                (Text(entry.month + " ").font(.system(size: 14, design: .serif)) + Text(entry.year).font(.system(size: 14, design: .serif).italic()))
                    .foregroundColor(.white.opacity(0.9)).lineLimit(1)
            }
            HStack(alignment: .top, spacing: 6) {
                ForEach(Array(entry.week.enumerated()), id: \.offset) { _, c in
                    Tap(url: SotdLinks.playFrom(c.slug)) {
                        VStack(spacing: 4) {
                            Color.clear.aspectRatio(1, contentMode: .fit)
                                .overlay(Art(cover: c.cover, radius: 6))
                                .overlay(RoundedRectangle(cornerRadius: 7).stroke(Color.white, lineWidth: c.today ? 2 : 0).padding(-1))
                            Text(c.weekday).font(.system(size: 8, weight: .semibold)).kerning(0.6).foregroundColor(.white.opacity(0.75))
                            Text("\(c.day)").font(.system(size: 13, design: .serif)).foregroundColor(.white)
                        }
                    }
                    .frame(maxWidth: .infinity)
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

// ---------- what's playing on the member's other devices ----------
struct ElseView: View {
    let entry: ElseEntry
    let size: WSize

    var caption: String {
        switch entry.state {
        case .playing: return "On your \(entry.label) now"
        case .paused: return "Paused on your \(entry.label)"
        case .here: return "Playing on this iPhone"
        default: return "Your other devices"
        }
    }

    var body: some View {
        switch entry.state {
        case .signedOut, .nothing:
            VStack(alignment: .leading, spacing: 6) {
                Image(systemName: "hifispeaker.2").font(.system(size: size == .small ? 22 : 26)).foregroundColor(.white.opacity(0.9))
                Spacer(minLength: 0)
                Caps(text: "Your other devices", size: 9)
                Text(entry.state == .signedOut ? "Sign in to see what’s playing on your other devices."
                                               : "Nothing’s playing on your other devices.")
                    .font(.system(size: size == .small ? 13 : 15, design: .serif)).foregroundColor(.white).lineLimit(4).minimumScaleFactor(0.85)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        default:
            if size == .small {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .top) {
                        Art(cover: entry.cover).frame(width: 62, height: 62)
                        Spacer(minLength: 0)
                        if entry.state != .here { Image(systemName: "arrow.down.forward.circle.fill").font(.system(size: 20)).foregroundColor(.white.opacity(0.9)) }
                    }
                    Spacer(minLength: 0)
                    Caps(text: caption, size: 8, opacity: 0.85)
                    Text(entry.title).font(.system(size: 14, design: .serif)).foregroundColor(.white).lineLimit(2)
                    Caps(text: entry.artist, size: 8, opacity: 0.75)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            } else {
                HStack(spacing: 14) {
                    Art(cover: entry.cover).frame(width: 112, height: 112)
                    VStack(alignment: .leading, spacing: 4) {
                        Caps(text: caption, size: 10, opacity: 0.85)
                        Text(entry.title).font(.system(size: 20, design: .serif)).foregroundColor(.white).lineLimit(2)
                        Caps(text: entry.artist, size: 11, opacity: 0.85)
                        Spacer(minLength: 0)
                        if entry.state != .here {
                            HStack(spacing: 5) {
                                Image(systemName: "play.fill").font(.system(size: 9, weight: .semibold))
                                Text("Carry on here").font(.system(size: 11, weight: .semibold))
                            }
                            .foregroundColor(.white).padding(.horizontal, 12).padding(.vertical, 7)
                            .background(Capsule().fill(Color.white.opacity(0.2)))
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                }
            }
        }
    }

    var tap: URL { return entry.state == .playing || entry.state == .paused ? SotdLinks.act("carry") : SotdLinks.calendar }
}
