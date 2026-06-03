import AppKit
import SwiftUI

final class SettingsWindowController: NSWindowController, NSWindowDelegate {
    private weak var daemonManager: DaemonManager?

    init(daemonManager: DaemonManager, settingsStore: SettingsStore, authManager: AuthManager, mcpManager: MCPManager) {
        self.daemonManager = daemonManager

        let view = SettingsRootView(store: settingsStore, daemonManager: daemonManager, authManager: authManager, mcpManager: mcpManager)
        let hosting = NSHostingController(rootView: view)
        hosting.sizingOptions = []

        let window = NSWindow(contentViewController: hosting)
        window.title = "Settings"
        window.styleMask = [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView]
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.setContentSize(NSSize(width: 900, height: 620))
        window.minSize = NSSize(width: 800, height: 560)
        window.center()
        window.isOpaque = true
        window.backgroundColor = NSColor(name: nil) { appearance in
            switch appearance.bestMatch(from: [.aqua, .darkAqua]) {
            case .darkAqua: return NSColor(red: 0x0F/255, green: 0x11/255, blue: 0x15/255, alpha: 1)
            default:        return NSColor(red: 0xF7/255, green: 0xF8/255, blue: 0xF5/255, alpha: 1)
            }
        }

        super.init(window: window)
        window.delegate = self
    }

    required init?(coder: NSCoder) { nil }

    func show() {
        setupMainMenuIfNeeded()
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }

    private func setupMainMenuIfNeeded() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(
            NSMenuItem(title: "Quit Patcha", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        )
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)

        let windowMenuItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(
            NSMenuItem(title: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        )
        windowMenu.addItem(
            NSMenuItem(title: "Minimize", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m")
        )
        let fullScreenItem = NSMenuItem(title: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fullScreenItem.keyEquivalentModifierMask = [.command, .control]
        windowMenu.addItem(fullScreenItem)
        windowMenuItem.submenu = windowMenu
        mainMenu.addItem(windowMenuItem)

        NSApp.mainMenu = mainMenu
        NSApp.windowsMenu = windowMenu
    }
}
