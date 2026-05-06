import AppKit
import SwiftUI

final class SettingsWindowController: NSWindowController, NSWindowDelegate {
    private let store: SettingsStore
    private weak var daemonManager: DaemonManager?

    init(daemonManager: DaemonManager) {
        self.store = SettingsStore()
        self.daemonManager = daemonManager

        let view = SettingsView(store: store, onSave: { daemonManager.restart() })
        let hosting = NSHostingController(rootView: view)
        hosting.sizingOptions = .preferredContentSize

        let window = NSWindow(contentViewController: hosting)
        window.title = "Patcha Settings"
        window.styleMask = [.titled, .closable]
        window.center()

        super.init(window: window)
        window.delegate = self
    }

    required init?(coder: NSCoder) { nil }

    func show() {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}
