import AppKit

struct AppEntry: Identifiable {
    let id: String
    let name: String
    let icon: NSImage?
    var isExcluded: Bool
}
