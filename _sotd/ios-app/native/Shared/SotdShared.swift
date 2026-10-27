import Foundation
import Security

// The member's sign-in, kept in the keychain the app and its widgets share (the access group below, in both their
// entitlements): the calendar hands it over when a member is signed in (SotdSiri.member), and takes it back on signing
// out; the "On your other device" widget reads it to ask the Worker what's playing. With it, this device's own id in
// the calendar, so the widget knows a song noted from here. Readable once the phone has been unlocked after starting.
// Also kept there (from iOS 26): the widget's push token from Apple ("push"), and the sign-in it was last handed to the
// Worker with ("sent"), so the Worker can tell the widget to look again the moment what's playing changes.
enum SotdShared {
    static let group = "X5H954758M.io.github.cooperking.sotd.shared"
    static let service = "io.github.cooperking.sotd.member"

    static func base(_ account: String) -> [String: Any] {
        return [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                kSecAttrAccount as String: account, kSecAttrAccessGroup as String: group]
    }

    static func put(_ account: String, _ v: [String: String]?) {
        SecItemDelete(base(account) as CFDictionary)
        guard let v = v, let data = try? JSONSerialization.data(withJSONObject: v) else { return }
        var q = base(account)
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(q as CFDictionary, nil)
    }

    static func get(_ account: String) -> [String: String]? {
        var q = base(account)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
        return (try? JSONSerialization.jsonObject(with: d)) as? [String: String]
    }

    // the member: { token, device }
    static func save(_ v: [String: String]?) { put("member", v) }
    static func load() -> [String: String]? { return get("member") }

    // the widget's push token: { push, sent }
    static func savePush(_ v: [String: String]?) { put("widgetpush", v) }
    static func loadPush() -> [String: String]? { return get("widgetpush") }
}
