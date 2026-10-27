import WidgetKit
import SwiftUI
import UIKit

// The Home Screen widget: the latest Song of the Day (the site's songs/today.json, written with the share pages), its
// cover, title, artist and day, on that month's colours. Tapping it opens the app at the day. It looks again every
// 45 minutes, keeping the last one it had if the site can't be reached.

struct SongEntry: TimelineEntry {
    let date: Date
    let title: String
    let artist: String
    let day: String
    let url: URL?
    let cover: UIImage?
    let ground: Color
    let glow: Color

    static let sample = SongEntry(date: Date(), title: "Song of the Day", artist: "John’s Song of the Day", day: "",
                                  url: URL(string: "https://cooperking.github.io/songs/"), cover: nil,
                                  ground: Color(hex: "#652bee"), glow: Color(hex: "#ff2bb8"))
}

extension Color {
    init(hex: String) {
        let v = UInt64(hex.replacingOccurrences(of: "#", with: ""), radix: 16) ?? 0x652bee
        self.init(red: Double((v >> 16) & 255) / 255, green: Double((v >> 8) & 255) / 255, blue: Double(v & 255) / 255)
    }
}

enum Feed {
    static let url = URL(string: "https://cooperking.github.io/songs/today.json")!

    static func entry(_ data: Data, cover: Data?) -> SongEntry? {
        guard let j = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], let title = j["title"] as? String else { return nil }
        let glow = (j["glow"] as? [String])?.first ?? "#ff2bb8"
        return SongEntry(date: Date(), title: title, artist: j["artist"] as? String ?? "", day: j["day"] as? String ?? "",
                         url: URL(string: j["url"] as? String ?? "https://cooperking.github.io/songs/"),
                         cover: cover.flatMap { UIImage(data: $0) },
                         ground: Color(hex: j["ground"] as? String ?? "#652bee"), glow: Color(hex: glow))
    }

    static func fetch(_ done: @escaping (SongEntry?) -> Void) {
        let keep = UserDefaults.standard
        func kept() -> SongEntry? { return keep.data(forKey: "feed").flatMap { entry($0, cover: keep.data(forKey: "cover")) } }
        var rq = URLRequest(url: url)
        rq.cachePolicy = .reloadIgnoringLocalCacheData
        rq.timeoutInterval = 12
        URLSession.shared.dataTask(with: rq) { data, resp, _ in
            guard let data = data, (resp as? HTTPURLResponse)?.statusCode == 200,
                  let j = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { done(kept()); return }
            keep.set(data, forKey: "feed")
            let c = (j["cover"] as? String).flatMap { URL(string: $0) }
            if let c = c, keep.string(forKey: "coverURL") != c.absoluteString {
                keep.removeObject(forKey: "cover")
                URLSession.shared.dataTask(with: c) { img, _, _ in
                    if let img = img, UIImage(data: img) != nil { keep.set(img, forKey: "cover"); keep.set(c.absoluteString, forKey: "coverURL") }
                    done(kept())
                }.resume()
            } else {
                done(kept())
            }
        }.resume()
    }
}

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> SongEntry { return .sample }

    func getSnapshot(in context: Context, completion: @escaping (SongEntry) -> Void) {
        Feed.fetch { completion($0 ?? .sample) }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<SongEntry>) -> Void) {
        Feed.fetch { e in
            let next = Date().addingTimeInterval(e == nil ? 20 * 60 : 45 * 60)
            completion(Timeline(entries: [e ?? .sample], policy: .after(next)))
        }
    }
}

struct TodayView: View {
    @Environment(\.widgetFamily) var family
    let entry: SongEntry

    var body: some View {
        content
            .widgetURL(entry.url)
            .sotdBackground(background)
    }

    var background: some View {
        ZStack {
            entry.ground
            RadialGradient(gradient: Gradient(colors: [entry.glow.opacity(0.85), entry.glow.opacity(0)]),
                           center: .topTrailing, startRadius: 0, endRadius: 240)
        }
    }

    @ViewBuilder var content: some View {
        if family == .systemSmall {
            VStack(alignment: .leading, spacing: 4) {
                art.frame(width: 62, height: 62)
                Spacer(minLength: 0)
                Text(entry.title).font(.system(size: 15, design: .serif)).foregroundColor(.white).lineLimit(2)
                Text(entry.artist.uppercased()).font(.system(size: 9, weight: .semibold)).kerning(0.8)
                    .foregroundColor(Color.white.opacity(0.8)).lineLimit(1)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        } else {
            HStack(spacing: 14) {
                art.frame(width: 112, height: 112)
                VStack(alignment: .leading, spacing: 4) {
                    Text(entry.day.uppercased()).font(.system(size: 10, weight: .semibold)).kerning(1)
                        .foregroundColor(Color.white.opacity(0.75)).lineLimit(1)
                    Text(entry.title).font(.system(size: 20, design: .serif)).foregroundColor(.white).lineLimit(2)
                    Text(entry.artist.uppercased()).font(.system(size: 11, weight: .semibold)).kerning(1)
                        .foregroundColor(Color.white.opacity(0.85)).lineLimit(1)
                    Spacer(minLength: 0)
                    Text("JOHN’S SONG OF THE DAY").font(.system(size: 9, weight: .semibold)).kerning(1.2)
                        .foregroundColor(Color.white.opacity(0.6)).lineLimit(1)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            }
        }
    }

    @ViewBuilder var art: some View {
        if let c = entry.cover {
            Image(uiImage: c).resizable().aspectRatio(contentMode: .fill)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        } else {
            RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.white.opacity(0.18))
                .overlay(Image(systemName: "music.note").foregroundColor(.white))
        }
    }
}

extension View {
    // iOS 17's widget background (it asks for this), else a plain one with the margins it would have given
    @ViewBuilder func sotdBackground<B: View>(_ b: B) -> some View {
        if #available(iOSApplicationExtension 17.0, *) {
            self.containerBackground(for: .widget) { b }
        } else {
            self.padding(16).background(b)
        }
    }
}

struct TodayWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "today", provider: Provider()) { entry in
            TodayView(entry: entry)
        }
        .configurationDisplayName("Song of the Day")
        .description("Today’s song: tap it to open its day.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

@main
struct SotdWidgets: WidgetBundle {
    var body: some Widget {
        TodayWidget()
    }
}
