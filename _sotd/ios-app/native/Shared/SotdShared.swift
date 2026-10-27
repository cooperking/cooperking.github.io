import Foundation
import Security

// The member's sign-in, kept in the keychain the app and its widgets share (the access group below, in both their
// entitlements): the calendar hands it over when a member is signed in (SotdSiri.member), and takes it back on signing
// out; the "On your other device" widget reads it to ask the Worker what's playing. With it, this device's own id in
// the calendar, so the widget knows a song noted from here. Readable once the phone has been unlocked after starting.
enum SotdShared {
    static let group = "X5H954758M.io.github.cooperking.sotd.shared"
    static let service = "io.github.cooperking.sotd.member"

    static func base() -> [String: Any] {
        return [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                kSecAttrAccount as String: "member", kSecAttrAccessGroup as String: group]
    }

    static func save(_ v: [String: String]?) {
        SecItemDelete(base() as CFDictionary)
        guard let v = v, let data = try? JSONSerialization.data(withJSONObject: v) else { return }
        var q = base()
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(q as CFDictionary, nil)
    }

    static func load() -> [String: String]? {
        var q = base()
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
        return (try? JSONSerialization.jsonObject(with: d)) as? [String: String]
    }
}
