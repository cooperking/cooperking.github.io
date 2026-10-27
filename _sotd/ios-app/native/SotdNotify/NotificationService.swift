import UserNotifications

// Each notification's picture (the day's card, or John's own picture on a message): the Worker sends its address as
// "pic" with mutable-content, and this fetches it and puts it on the notification before iOS shows it. Without a
// picture, or if it can't be fetched in time, the notification shows as it came.
class NotificationService: UNNotificationServiceExtension {
    var handler: ((UNNotificationContent) -> Void)?
    var best: UNMutableNotificationContent?

    override func didReceive(_ request: UNNotificationRequest, withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void) {
        handler = contentHandler
        let content = request.content.mutableCopy() as? UNMutableNotificationContent
        best = content
        guard let content = content, let s = request.content.userInfo["pic"] as? String, let url = URL(string: s), url.scheme == "https" else {
            contentHandler(request.content)
            return
        }
        URLSession.shared.downloadTask(with: url) { [weak self] loc, resp, _ in
            if let loc = loc {
                let ext = (resp?.mimeType == "image/png") ? "png" : "jpg"
                let file = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent(UUID().uuidString + "." + ext)
                if (try? FileManager.default.moveItem(at: loc, to: file)) != nil,
                   let a = try? UNNotificationAttachment(identifier: "pic", url: file, options: nil) {
                    content.attachments = [a]
                }
            }
            self?.finish(content)
        }.resume()
    }

    private func finish(_ content: UNNotificationContent) {
        guard let h = handler else { return }
        handler = nil
        h(content)
    }

    override func serviceExtensionTimeWillExpire() {
        if let b = best { finish(b) }
    }
}
