import Foundation
import SwiftUI
import WidgetKit
import ImageIO
import AppIntents
#if canImport(UIKit)
import UIKit
typealias PlatformImage = UIImage
extension Image { init(platform: PlatformImage) { self.init(uiImage: platform) } }
func platformImage(_ cg: CGImage) -> PlatformImage { return UIImage(cgImage: cg) }
#else
import AppKit
typealias PlatformImage = NSImage
extension Image { init(platform: PlatformImage) { self.init(nsImage: platform) } }
func platformImage(_ cg: CGImage) -> PlatformImage { return NSImage(cgImage: cg, size: NSSize(width: cg.width, height: cg.height)) }
#endif

// What the widgets show, and where they come from: the site's songs/today.json (the latest song), songs/days.json
// (every day out on the calendar, and each month's colours: written with the share pages), the covers (small copies
// kept on the phone), and the Worker's note of what the member is playing (the "On your other device" widget).
// Shared by the widgets and, for pictures of them, a small Mac program (no iPhone-only parts here).

enum SotdLinks {
    static let site = "https://cooperking.github.io/songs/"
    static let worker = "https://sotd-playlists.cooperking.workers.dev"
    static func act(_ a: String) -> URL { return URL(string: site + "?do=" + a)! }   // today, shuffle, mine, carry
    static func playFrom(_ slug: String) -> URL { return URL(string: site + "?do=all#" + slug)! }   // Play all from that day
    static let calendar = URL(string: site)!
    static var plain = false   // (the pictures of the widgets: taps drawn without their Link)
}

extension Color {
    init(hex: String) {
        let v = UInt64(hex.replacingOccurrences(of: "#", with: ""), radix: 16) ?? 0x652bee
        self.init(red: Double((v >> 16) & 255) / 255, green: Double((v >> 8) & 255) / 255, blue: Double(v & 255) / 255)
    }
}

struct Palette {
    let ground: Color
    let glow: Color
    // the calendar's month colours (the site's PALETTES), for when days.json hasn't come yet
    static let months: [(String, String)] = [
        ("#0c1445", "#0094de"), ("#652bee", "#3333ff"), ("#11970c", "#429e00"), ("#b613ec", "#fc00e7"), ("#096a42", "#00a336"), ("#ec1a13", "#fa5300"), ("#1346ec", "#008ff5"), ("#d85a06", "#c47c00"), ("#0b86d3", "#0098b0"), ("#ec135b", "#ff3344"), ("#09696d", "#009e77"), ("#ec13c1", "#ff3399")]
    static func month(_ m: Int, from list: [Palette] = []) -> Palette {
        let i = max(0, min(11, m - 1))
        if i < list.count { return list[i] }
        return Palette(ground: Color(hex: months[i].0), glow: Color(hex: months[i].1))
    }
}

// what a tap plays
enum TapChoice: String {
    case playAll, shuffle, mine, day
    func url(day: URL?) -> URL {
        switch self {
        case .playAll: return SotdLinks.act("today")
        case .shuffle: return SotdLinks.act("shuffle")
        case .mine: return SotdLinks.act("mine")
        case .day: return day ?? SotdLinks.calendar
        }
    }
}

// the size a view is drawn for (the widget's family, or the Mac program's choice)
enum WSize { case small, medium, large, rect, circle, inline }

// ---------- the latest song ----------
struct SongEntry: TimelineEntry {
    let date: Date
    let title: String
    let artist: String
    let day: String
    let dayURL: URL?
    let cover: PlatformImage?
    let ground: Color
    let glow: Color
    var tap: URL

    static let sample = SongEntry(date: Date(), title: "Song of the Day", artist: "John’s Song of the Day", day: "", dayURL: nil, cover: nil,
                                  ground: Color(hex: "#652bee"), glow: Color(hex: "#ff2bb8"), tap: SotdLinks.act("today"))
}

enum Feed {
    static let url = URL(string: SotdLinks.site + "today.json")!

    static func entry(_ data: Data, cover: Data?, tap: TapChoice) -> SongEntry? {
        guard let j = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], let title = j["title"] as? String else { return nil }
        let glow = (j["glow"] as? [String])?.first ?? "#ff2bb8"
        let day = URL(string: j["url"] as? String ?? SotdLinks.site)
        return SongEntry(date: Date(), title: title, artist: j["artist"] as? String ?? "", day: j["day"] as? String ?? "", dayURL: day,
                         cover: cover.flatMap { Thumbs.image($0, side: 360) },
                         ground: Color(hex: j["ground"] as? String ?? "#652bee"), glow: Color(hex: glow), tap: tap.url(day: day))
    }

    // the latest song; the last one had if the site can't be reached
    static func fetch(tap: TapChoice, _ done: @escaping (SongEntry?) -> Void) {
        let keep = UserDefaults.standard
        func kept() -> SongEntry? { return keep.data(forKey: "feed").flatMap { entry($0, cover: keep.data(forKey: "cover"), tap: tap) } }
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
                    if let img = img, Thumbs.image(img, side: 64) != nil { keep.set(img, forKey: "cover"); keep.set(c.absoluteString, forKey: "coverURL") }
                    done(kept())
                }.resume()
            } else {
                done(kept())
            }
        }.resume()
    }
}

// ---------- every day ----------
struct Day {
    let date: String   // 2026-10-27
    let slug: String   // 27-10-2026
    let title: String
    let artist: String
    let cover: String
    let track: String
    var year: Int { return Int(date.prefix(4)) ?? 2026 }
    var month: Int { return Int(date.dropFirst(5).prefix(2)) ?? 1 }
    var dayOfMonth: Int { return Int(date.suffix(2)) ?? 1 }
}

enum Days {
    static let url = URL(string: SotdLinks.site + "days.json")!

    static func parse(_ data: Data) -> (days: [Day], palettes: [Palette])? {
        guard let j = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], let ds = j["days"] as? [[String: Any]] else { return nil }
        let days = ds.compactMap { d -> Day? in
            guard let date = d["date"] as? String, let slug = d["slug"] as? String else { return nil }
            return Day(date: date, slug: slug, title: d["title"] as? String ?? "", artist: d["artist"] as? String ?? "",
                       cover: d["cover"] as? String ?? "", track: d["track"] as? String ?? "")
        }
        let pals = ((j["palettes"] as? [[String: Any]]) ?? []).map { p in
            Palette(ground: Color(hex: p["ground"] as? String ?? "#652bee"), glow: Color(hex: (p["glow"] as? [String])?.first ?? "#ff2bb8"))
        }
        return (days, pals)
    }

    // every day; the last list had if the site can't be reached (looked for again at most every 20 minutes)
    static func fetch(_ done: @escaping ([Day], [Palette]) -> Void) {
        let keep = UserDefaults.standard
        func kept() -> ([Day], [Palette]) { return keep.data(forKey: "days").flatMap { parse($0) }.map { ($0.days, $0.palettes) } ?? ([], []) }
        if Date().timeIntervalSince1970 - keep.double(forKey: "daysAt") < 20 * 60, keep.data(forKey: "days") != nil { let k = kept(); done(k.0, k.1); return }
        var rq = URLRequest(url: url)
        rq.cachePolicy = .reloadIgnoringLocalCacheData
        rq.timeoutInterval = 12
        URLSession.shared.dataTask(with: rq) { data, resp, _ in
            if let data = data, (resp as? HTTPURLResponse)?.statusCode == 200, parse(data) != nil {
                keep.set(data, forKey: "days"); keep.set(Date().timeIntervalSince1970, forKey: "daysAt")
            }
            let k = kept(); done(k.0, k.1)
        }.resume()
    }
}

// ---------- covers: small copies kept on the phone (the site's 300-pixel covers, as they come), made smaller to show ----------
enum Thumbs {
    static var dir: URL {
        let d = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("covers", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }
    static func file(_ url: String) -> URL { return dir.appendingPathComponent(String(url.split(separator: "/").last ?? "x") + ".jpg") }

    static func image(_ data: Data, side: Int) -> PlatformImage? {
        guard let src = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        let opts: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: side,
                                     kCGImageSourceCreateThumbnailWithTransform: true]
        guard let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) else { return nil }
        return platformImage(cg)
    }

    static func kept(_ url: String, side: Int) -> PlatformImage? {
        guard !url.isEmpty, let d = try? Data(contentsOf: file(url)) else { return nil }
        return image(d, side: side)
    }

    // the covers not kept yet, fetched together (8 seconds at most); then done
    static func fetch(_ urls: [String], _ done: @escaping () -> Void) {
        let need = urls.filter { !$0.isEmpty && !FileManager.default.fileExists(atPath: file($0).path) }
        if need.isEmpty { done(); return }
        let g = DispatchGroup()
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 8
        cfg.httpMaximumConnectionsPerHost = 6
        let session = URLSession(configuration: cfg)
        for u in need {
            guard let url = URL(string: u) else { continue }
            g.enter()
            session.dataTask(with: url) { data, resp, _ in
                if let data = data, (resp as? HTTPURLResponse)?.statusCode == 200, image(data, side: 32) != nil { try? data.write(to: file(u)) }
                g.leave()
            }.resume()
        }
        g.notify(queue: .global()) { session.finishTasksAndInvalidate(); prune(); done() }
    }

    static func prune() {   // the covers kept: the 400 newest
        let fm = FileManager.default
        guard let files = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey]), files.count > 400 else { return }
        let dated = files.map { ($0, (try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast) }
        for (f, _) in dated.sorted(by: { $0.1 > $1.1 }).dropFirst(400) { try? fm.removeItem(at: f) }
    }
}

// ---------- the calendar ----------
struct DayCell {
    let day: Int
    let slug: String
    let title: String
    let artist: String
    let weekday: String   // MON
    let cover: PlatformImage?
    let today: Bool
    let song: Bool
}

struct CalendarEntry: TimelineEntry {
    let date: Date
    let month: String    // October
    let year: String     // 2026
    let cells: [DayCell?]   // the month's grid, Monday first (nil: before the 1st, after the last)
    let week: [DayCell]     // the last seven days with songs, oldest first
    let palette: Palette
    let back: Bool        // an earlier month with songs
    let forward: Bool     // a later month (this month is the last)
    let offset: Int       // months back from this one

    static func sample() -> CalendarEntry {
        return CalendarEntry(date: Date(), month: "October", year: "2026", cells: [], week: [], palette: Palette.month(10), back: false, forward: false, offset: 0)
    }
}

enum CalendarModel {
    static var cal: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Europe/London") ?? .current
        c.firstWeekday = 2
        return c
    }
    static let monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    static let dayNames = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]

    // the month `offset` months before this one (no earlier than the first with a song), its covers as kept
    static func entry(days: [Day], palettes: [Palette], offset: Int, now: Date = Date(), side: Int = 150) -> CalendarEntry {
        let c = cal
        let thisMonth = c.date(from: c.dateComponents([.year, .month], from: now))!
        let first = days.first.flatMap { d in c.date(from: DateComponents(year: d.year, month: d.month, day: 1)) } ?? thisMonth
        let maxBack = max(0, (c.dateComponents([.month], from: first, to: thisMonth).month ?? 0))
        let off = max(0, min(offset, maxBack))
        let start = c.date(byAdding: .month, value: -off, to: thisMonth)!
        let ym = c.dateComponents([.year, .month], from: start)
        let y = ym.year!, m = ym.month!
        let count = c.range(of: .day, in: .month, for: start)!.count
        let lead = (c.component(.weekday, from: start) + 5) % 7   // Monday first
        let todayKey = key(c, now)
        var byDate: [String: Day] = [:]
        for d in days { byDate[d.date] = d }
        var cells: [DayCell?] = Array(repeating: nil, count: lead)
        for n in 1...count {
            let k = String(format: "%04d-%02d-%02d", y, m, n)
            let wd = dayNames[(lead + n - 1 + 1) % 7]
            if let d = byDate[k] {
                cells.append(DayCell(day: n, slug: d.slug, title: d.title, artist: d.artist, weekday: wd, cover: Thumbs.kept(d.cover, side: side), today: k == todayKey, song: true))
            } else {
                cells.append(DayCell(day: n, slug: "", title: "", artist: "", weekday: wd, cover: nil, today: k == todayKey, song: false))
            }
        }
        while cells.count % 7 != 0 { cells.append(nil) }
        let week = days.suffix(7).map { d -> DayCell in
            let dt = c.date(from: DateComponents(year: d.year, month: d.month, day: d.dayOfMonth)) ?? now
            return DayCell(day: d.dayOfMonth, slug: d.slug, title: d.title, artist: d.artist, weekday: dayNames[c.component(.weekday, from: dt) - 1],
                           cover: Thumbs.kept(d.cover, side: side), today: d.date == todayKey, song: true)
        }
        return CalendarEntry(date: now, month: monthNames[m - 1], year: String(y), cells: cells, week: week,
                             palette: Palette.month(m, from: palettes), back: off < maxBack, forward: off > 0, offset: off)
    }

    // the covers the month `offset` back needs (and the last seven days')
    static func covers(days: [Day], offset: Int, now: Date = Date()) -> [String] {
        let c = cal
        let start = c.date(byAdding: .month, value: -max(0, offset), to: c.date(from: c.dateComponents([.year, .month], from: now))!)!
        let ym = c.dateComponents([.year, .month], from: start)
        let p = String(format: "%04d-%02d-", ym.year!, ym.month!)
        return days.filter { $0.date.hasPrefix(p) }.map { $0.cover } + days.suffix(7).map { $0.cover }
    }

    static func key(_ c: Calendar, _ d: Date) -> String {
        let x = c.dateComponents([.year, .month, .day], from: d)
        return String(format: "%04d-%02d-%02d", x.year!, x.month!, x.day!)
    }
}

// the month the calendar widget shows, months back from this one (its arrows, iOS 17 on)
enum MonthPick {
    static let key = "calendarOffset"
    static var offset: Int {
        get { return UserDefaults.standard.integer(forKey: key) }
        set { UserDefaults.standard.set(max(0, newValue), forKey: key) }
    }
}

// its arrows: a month back or on (step -1, 1), or this month (0)
@available(iOS 17.0, macOS 14.0, *)
struct MonthIntent: AppIntent {
    static var title: LocalizedStringResource = "Change the calendar’s month"
    static var isDiscoverable: Bool = false
    @Parameter(title: "Step") var step: Int
    init() { step = 0 }
    init(step: Int) { self.step = step }
    func perform() async throws -> some IntentResult {
        MonthPick.offset = step == 0 ? 0 : MonthPick.offset - step
        return .result()
    }
}

// ---------- what's playing on the member's other devices ----------
struct ElseEntry: TimelineEntry {
    enum State { case signedOut, nothing, playing, paused, here }
    let date: Date
    let state: State
    let label: String     // Mac
    let title: String
    let artist: String
    let cover: PlatformImage?
    let palette: Palette

    static let sample = ElseEntry(date: Date(), state: .playing, label: "Mac", title: "Song of the Day", artist: "John’s Song of the Day", cover: nil, palette: Palette.month(10))
}

enum Elsewhere {
    // the Worker's note for this member (its token, from the shared keychain), with the song from the days list
    static func fetch(token: String?, device: String?, _ done: @escaping (ElseEntry) -> Void) {
        let month = CalendarModel.cal.component(.month, from: Date())
        guard let token = token, !token.isEmpty, let url = URL(string: SotdLinks.worker + "/members/now") else {
            done(ElseEntry(date: Date(), state: .signedOut, label: "", title: "", artist: "", cover: nil, palette: Palette.month(month))); return
        }
        var rq = URLRequest(url: url)
        rq.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        rq.cachePolicy = .reloadIgnoringLocalCacheData
        rq.timeoutInterval = 10
        URLSession.shared.dataTask(with: rq) { data, resp, _ in
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let j = data.flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] }
            Days.fetch { days, pals in
                let pal = Palette.month(month, from: pals)
                if code == 401 { done(ElseEntry(date: Date(), state: .signedOut, label: "", title: "", artist: "", cover: nil, palette: pal)); return }
                guard let j = j, let rec = j["rec"] as? [String: Any], let t = rec["t"] as? String,
                      let now = j["now"] as? Double, let at = rec["at"] as? Double else {
                    done(ElseEntry(date: Date(), state: .nothing, label: "", title: "", artist: "", cover: nil, palette: pal)); return
                }
                let age = now - at, playing = rec["playing"] as? Bool ?? false
                if age > 12 * 3600e3 { done(ElseEntry(date: Date(), state: .nothing, label: "", title: "", artist: "", cover: nil, palette: pal)); return }
                let day = days.first { $0.track == t } ?? days.first { $0.date == (rec["d"] as? String ?? "") }
                let label = rec["label"] as? String ?? "other device"
                let state: ElseEntry.State = (rec["dev"] as? String) == device && device != nil ? .here : (playing && age <= 20 * 60e3 ? .playing : .paused)
                let finish = {
                    done(ElseEntry(date: Date(), state: state, label: label, title: day?.title ?? "Song of the Day", artist: day?.artist ?? "",
                                   cover: day.flatMap { Thumbs.kept($0.cover, side: 360) }, palette: day.map { Palette.month($0.month, from: pals) } ?? pal))
                }
                if let d = day { Thumbs.fetch([d.cover]) { finish() } } else { finish() }
            }
        }.resume()
    }
}
