import Foundation
import Capacitor
import Contacts
import ContactsUI

// Apple's own contact picker, for the admin page's Members tab (John, 29 Oct 2026: a new member's name and email from
// his contacts): pick() shows it and gives back the person chosen, { name, emails: [addresses] }, or rejects
// "cancelled" when it's put away. The picker runs outside the app, so the app gets only the one contact chosen and
// needs no permission for the address book (nor an NSContactsUsageDescription). From build 22.
@objc(SotdContactsPlugin)
public class SotdContactsPlugin: CAPPlugin, CAPBridgedPlugin, CNContactPickerDelegate {
    public let identifier = "SotdContactsPlugin"
    public let jsName = "SotdContacts"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "pick", returnType: CAPPluginReturnPromise)
    ]
    private var waiting: CAPPluginCall?

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

    public func contactPicker(_ picker: CNContactPickerViewController, didSelect contact: CNContact) {
        let has = { (k: String) in contact.isKeyAvailable(k) }
        var name = [has(CNContactGivenNameKey) ? contact.givenName : "", has(CNContactFamilyNameKey) ? contact.familyName : ""]
            .filter { !$0.isEmpty }.joined(separator: " ")
        if name.isEmpty, has(CNContactNicknameKey) { name = contact.nickname }
        if name.isEmpty, has(CNContactOrganizationNameKey) { name = contact.organizationName }
        let emails = has(CNContactEmailAddressesKey) ? contact.emailAddresses.map { String($0.value) } : []
        finish(["name": name, "emails": emails])
    }

    public func contactPickerDidCancel(_ picker: CNContactPickerViewController) { finish(nil) }

    private func finish(_ data: [String: Any]?) {
        guard let call = waiting else { return }
        waiting = nil
        if let d = data { call.resolve(d) } else { call.reject("cancelled") }
    }
}
