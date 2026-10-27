import SwiftUI
import AppKit

// Pictures of the iPhone widgets, drawn on a Mac from the widgets' own views (native/SotdWidget/SotdModel.swift and
// SotdViews.swift) at an iPhone's widget sizes, with the site's latest song and a days list (the second argument).
// Run by .github/workflows/widgets-check.yml:  shots OUTDIR DAYS.json

@MainActor
func render<V: View, B: View>(_ out: String, _ name: String, _ w: CGFloat, _ h: CGFloat, _ bg: B, pad: CGFloat = 16, radius: CGFloat = 22, _ v: V) {
    let content = ZStack { bg; v.padding(pad) }
        .frame(width: w, height: h)
        .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
        .environment(\.colorScheme, .dark)
    let r = ImageRenderer(content: content)
    r.scale = 3
    guard let cg = r.cgImage, let png = NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:]) else { print("no picture:", name); return }
    try? png.write(to: URL(fileURLWithPath: out + "/" + name + ".png"))
    print("drew", name)
}

@main
struct Shots {
    @MainActor
    static func main() async {
        let args = CommandLine.arguments
        let out = args.count > 1 ? args[1] : "out"
        try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)
        let song: SongEntry = await withCheckedContinuation { k in Feed.fetch(tap: .playAll) { k.resume(returning: $0 ?? .sample) } }
        var days: [Day] = [], pals: [Palette] = []
        if args.count > 2, let d = FileManager.default.contents(atPath: args[2]), let p = Days.parse(d) { days = p.days; pals = p.palettes }
        await withCheckedContinuation { (k: CheckedContinuation<Void, Never>) in
            Thumbs.fetch(CalendarModel.covers(days: days, offset: 0) + CalendarModel.covers(days: days, offset: 1)) { k.resume() }
        }
        let cal = CalendarModel.entry(days: days, palettes: pals, offset: 0)
        let calBack = CalendarModel.entry(days: days, palettes: pals, offset: 1)
        let last = days.last
        let pal = cal.palette
        func elsewhere(_ s: ElseEntry.State) -> ElseEntry {
            return ElseEntry(date: Date(), state: s, label: "Mac", title: last?.title ?? "", artist: last?.artist ?? "",
                             cover: last.flatMap { Thumbs.kept($0.cover, side: 360) }, palette: pal)
        }
        let songBg = SotdBackground(ground: song.ground, glow: song.glow)
        let calBg = SotdBackground(ground: cal.palette.ground, glow: cal.palette.glow)
        let lock = LinearGradient(colors: [Color(hex: "#1d2b4a"), Color(hex: "#5a3a6e")], startPoint: .top, endPoint: .bottom)

        render(out, "1-today-small", 170, 170, songBg, TodayView(entry: song, size: .small))
        render(out, "2-today-medium", 364, 170, songBg, TodayView(entry: song, size: .medium))
        render(out, "3-play-medium", 364, 170, songBg, PlayView(entry: song))
        render(out, "4-calendar-large", 364, 382, calBg, CalendarView(entry: cal, size: .large, arrows: true))
        render(out, "5-calendar-large-month-before", 364, 382, SotdBackground(ground: calBack.palette.ground, glow: calBack.palette.glow),
               CalendarView(entry: calBack, size: .large, arrows: true))
        render(out, "6-calendar-medium", 364, 170, calBg, CalendarView(entry: cal, size: .medium, arrows: true))
        render(out, "7-elsewhere-small-playing", 170, 170, SotdBackground(ground: pal.ground, glow: pal.glow), ElseView(entry: elsewhere(.playing), size: .small))
        render(out, "8-elsewhere-medium-playing", 364, 170, SotdBackground(ground: pal.ground, glow: pal.glow), ElseView(entry: elsewhere(.playing), size: .medium))
        render(out, "9-elsewhere-medium-paused", 364, 170, SotdBackground(ground: pal.ground, glow: pal.glow), ElseView(entry: elsewhere(.paused), size: .medium))
        render(out, "10-elsewhere-small-nothing", 170, 170, SotdBackground(ground: pal.ground, glow: pal.glow), ElseView(entry: elsewhere(.nothing), size: .small))
        render(out, "11-elsewhere-small-signed-out", 170, 170, SotdBackground(ground: pal.ground, glow: pal.glow), ElseView(entry: elsewhere(.signedOut), size: .small))
        render(out, "12-lock-rectangular", 172, 76, lock, pad: 6, radius: 12, TodayView(entry: song, size: .rect).foregroundColor(.white))
        render(out, "13-lock-circular", 76, 76, lock, pad: 4, radius: 38, TodayView(entry: song, size: .circle).foregroundColor(.white))
        render(out, "14-lock-inline", 260, 24, lock, pad: 2, radius: 6, TodayView(entry: song, size: .inline).foregroundColor(.white))
    }
}
