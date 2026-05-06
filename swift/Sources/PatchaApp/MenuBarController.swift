import AppKit
import Combine

class MenuBarController: NSObject {
    private var statusItem: NSStatusItem
    private var daemonManager: DaemonManager
    private var cancellable: AnyCancellable?

    private lazy var statusMenuItem: NSMenuItem = {
        let item = NSMenuItem(title: "Status: Stopped", action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }()

    init(daemonManager: DaemonManager) {
        self.daemonManager = daemonManager
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        super.init()

        buildMenu()
        updateIcon(for: .stopped)

        cancellable = daemonManager.$status
            .receive(on: DispatchQueue.main)
            .sink { [weak self] newStatus in
                self?.updateIcon(for: newStatus)
                self?.updateStatusLabel(for: newStatus)
            }
    }

    private func buildMenu() {
        let menu = NSMenu()
        menu.delegate = self

        let titleItem = NSMenuItem(title: "Patcha", action: nil, keyEquivalent: "")
        titleItem.isEnabled = false
        menu.addItem(titleItem)

        menu.addItem(.separator())
        menu.addItem(statusMenuItem)
        menu.addItem(.separator())

        let restartItem = NSMenuItem(title: "Restart Daemon", action: #selector(restartDaemon), keyEquivalent: "r")
        restartItem.target = self
        menu.addItem(restartItem)

        menu.addItem(.separator())

        let permissionsItem = NSMenuItem(title: "Grant Permissions...", action: #selector(grantPermissions), keyEquivalent: "")
        permissionsItem.target = self
        menu.addItem(permissionsItem)

        menu.addItem(.separator())

        let quitItem = NSMenuItem(title: "Quit Patcha", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        statusItem.menu = menu
    }

    private func updateIcon(for daemonStatus: DaemonStatus) {
        guard let button = statusItem.button else { return }

        let alpha: CGFloat
        switch daemonStatus {
        case .running:               alpha = 1.0
        case .starting, .restarting: alpha = 0.5
        case .stopped, .failed:      alpha = 0.3
        }

        if let icon = bundledIcon() {
            icon.isTemplate = true
            button.image = icon
            button.alphaValue = alpha
        } else {
            button.title = "P"
        }
    }

    private func bundledIcon() -> NSImage? {
        guard let url = Bundle.main.url(forResource: "menubar-icon", withExtension: "svg"),
              let image = NSImage(contentsOf: url) else { return nil }
        image.size = NSSize(width: 18, height: 18)
        return image
    }

    private func updateStatusLabel(for daemonStatus: DaemonStatus) {
        switch daemonStatus {
        case .stopped:
            statusMenuItem.title = "Status: Stopped"
        case .starting:
            statusMenuItem.title = "Status: Starting..."
        case .running:
            statusMenuItem.title = "Status: Running"
        case .restarting(let attempt):
            statusMenuItem.title = "Status: Restarting (attempt \(attempt))"
        case .failed:
            statusMenuItem.title = "Status: Failed — restart limit reached"
        }
    }

    @objc private func restartDaemon() {
        daemonManager.restart()
    }

    @objc private func grantPermissions() {
        PermissionsManager.resetPromptFlags()
        PermissionsManager.requestIfNeeded()
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }
}

extension MenuBarController: NSMenuDelegate {
    func menuWillOpen(_ menu: NSMenu) {
        let allGranted = PermissionsManager.accessibilityGranted() && PermissionsManager.screenRecordingGranted()
        if let item = menu.items.first(where: { $0.action == #selector(grantPermissions) }) {
            item.isHidden = allGranted
        }
    }
}
