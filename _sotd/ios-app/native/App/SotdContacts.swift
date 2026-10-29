import Foundation
import Capacitor
import Contacts
import ContactsUI

// Apple's own contact picker, for the admin page's Members tab (John, 29 Oct 2026: a new member's name and email from
// his contacts): pick() shows it and gives back the person chosen, { name, emails: [addresses] }, or rejects
// "cancelled" when it's put away. pickMany() (from build 24) shows it with a tick by each person and Done, and gives back
// { people: [{ name, emails }] }: an invitation for each, to check, on the Members tab. The picker runs outside the
// app, so the app gets only the contacts chosen and needs no permission for the address book (nor an
// NSContactsUsageDescription). From build 22.
@objc(SotdContactsPlugin)
public class SotdContactsPlugin: CAPPlugin, CAPBridgedPlugin, CNContactPickerDelegate {
    public let identifier = "SotdContactsPlugin"
    public let jsName = "SotdContacts"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "pick", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "pickMany", returnType: CAPPluginReturnPromise)
    ]
    private var waiting: CAPPluginCall?
    private var many: SotdContactsMany?   // (the picker holds its delegate weakly)

    @objc func pick(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            if let old = self.waiting { old.reject("replaced") }
            self.waiting = call
            let picker = CNContactPickerViewController()
            picker.delegate = self
            picker.displayedPropertyKeys = [CNContactEmailAddressesKey]
            guard let vc = self.bridge?.viewController else { self.finish(nil); return }
            vc.present(picker, animated: true)
        }
    }

    // Several people: the picker allows that when its delegate takes a list (contactPicker(_:didSelect contacts:)), so
    // that's a delegate of its own, leaving pick()'s one person at a time.
    @objc func pickMany(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            if let old = self.waiting { old.reject("replaced") }
            self.waiting = call
            let many = SotdContactsMany { [weak self] list in
                guard let self = self else { return }
                self.many = nil
                if let l = list {
                    let people: [[String: Any]] = l.map { SotdContactsPlugin.person($0) }
                    self.finish(["people": people])
                } else { self.finish(nil) }
            }
            self.many = many
            let picker = CNContactPickerViewController()
            picker.delegate = many
            guard let vc = self.bridge?.viewController else { self.many = nil; self.finish(nil); return }
            vc.present(picker, animated: true)
        }
    }

    static func person(_ contact: CNContact) -> [String: Any] {
        let has = { (k: String) in contact.isKeyAvailable(k) }
        var name = [has(CNContactGivenNameKey) ? contact.givenName : "", has(CNContactFamilyNameKey) ? contact.familyName : ""]
            .filter { !$0.isEmpty }.joined(separator: " ")
        if name.isEmpty, has(CNContactNicknameKey) { name = contact.nickname }
        if name.isEmpty, has(CNContactOrganizationNameKey) { name = contact.organizationName }
        let emails = has(CNContactEmailAddressesKey) ? contact.emailAddresses.map { String($0.value) } : []
        return ["name": name, "emails": emails]
    }

    public func contactPicker(_ picker: CNContactPickerViewController, didSelect contact: CNContact) {
        finish(SotdContactsPlugin.person(contact))
    }

    public func contactPickerDidCancel(_ picker: CNContactPickerViewController) { finish(nil) }

    private func finish(_ data: [String: Any]?) {
        guard let call = waiting else { return }
        waiting = nil
        if let d = data { call.resolve(d) } else { call.reject("cancelled") }
    }
}

final class SotdContactsMany: NSObject, CNContactPickerDelegate {
    private let done: ([CNContact]?) -> Void
    init(_ done: @escaping ([CNContact]?) -> Void) { self.done = done }
    func contactPicker(_ picker: CNContactPickerViewController, didSelect contacts: [CNContact]) { done(contacts) }
    func contactPickerDidCancel(_ picker: CNContactPickerViewController) { done(nil) }
}
