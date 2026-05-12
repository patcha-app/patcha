import AppKit
import Combine

class MenuBarController: NSObject {
    private var statusItem: NSStatusItem
    private var daemonManager: DaemonManager
    private var settingsWindowController: SettingsWindowController
    private var cancellables = Set<AnyCancellable>()

    private lazy var statusMenuItem: NSMenuItem = {
        let item = NSMenuItem(title: "Starting up...", action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }()

    private lazy var grantAccessibilityItem: NSMenuItem = {
        let item = NSMenuItem(title: "Grant Accessibility Access...", action: #selector(openAccessibilityPrefs), keyEquivalent: "")
        item.target = self
        item.isHidden = true
        return item
    }()

    private lazy var grantScreenRecordingItem: NSMenuItem = {
        let item = NSMenuItem(title: "Grant Screen Recording Access...", action: #selector(openScreenRecordingPrefs), keyEquivalent: "")
        item.target = self
        item.isHidden = true
        return item
    }()

    private var resumeNowItem: NSMenuItem!

    init(daemonManager: DaemonManager, settingsWindowController: SettingsWindowController) {
        self.daemonManager = daemonManager
        self.settingsWindowController = settingsWindowController
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        super.init()

        buildMenu()
        updateIcon(for: .stopped)

        daemonManager.$status
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.updateMenuState() }
            .store(in: &cancellables)

        daemonManager.$pausedUntil
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.updateMenuState() }
            .store(in: &cancellables)

        NotificationCenter.default.addObserver(
            self,
            selector: #selector(appDidBecomeActive),
            name: NSApplication.didBecomeActiveNotification,
            object: nil
        )
        refreshPermissions()
    }

    private func buildMenu() {
        let menu = NSMenu()
        menu.delegate = self

        menu.addItem(grantAccessibilityItem)
        menu.addItem(grantScreenRecordingItem)

        menu.addItem(.separator())
        menu.addItem(buildPauseSubmenu())

        let restartItem = NSMenuItem(title: "Restart Daemon", action: #selector(restartDaemon), keyEquivalent: "r")
        restartItem.target = self
        menu.addItem(restartItem)

        menu.addItem(.separator())

        let settingsItem = NSMenuItem(title: "Open Settings", action: #selector(openSettings), keyEquivalent: ",")
        settingsItem.target = self
        settingsItem.image = nil
        menu.addItem(settingsItem)

        menu.addItem(buildResourcesSubmenu())

        menu.addItem(.separator())

        let quitItem = NSMenuItem(title: "Quit Patcha", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        statusItem.menu = menu
    }

    private func buildPauseSubmenu() -> NSMenuItem {
        let sub = NSMenu()

        let durations: [(String, Int)] = [
            ("30 minutes", 30 * 60),
            ("1 hour",     60 * 60),
            ("2 hours",   120 * 60),
            ("4 hours",   240 * 60),
        ]
        for (title, secs) in durations {
            let item = NSMenuItem(title: title, action: #selector(pauseFor(_:)), keyEquivalent: "")
            item.target = self
            item.tag = secs
            sub.addItem(item)
        }

        let tomorrowItem = NSMenuItem(title: "Until tomorrow", action: #selector(pauseUntilTomorrow), keyEquivalent: "")
        tomorrowItem.target = self
        sub.addItem(tomorrowItem)

        sub.addItem(.separator())

        resumeNowItem = NSMenuItem(title: "Resume Now", action: #selector(resumeNow), keyEquivalent: "")
        resumeNowItem.target = self
        resumeNowItem.isHidden = true
        sub.addItem(resumeNowItem)

        let pauseItem = NSMenuItem(title: "Pause Recording", action: nil, keyEquivalent: "")
        pauseItem.submenu = sub
        return pauseItem
    }

    private func buildResourcesSubmenu() -> NSMenuItem {
        let sub = NSMenu()
        let visitItem = NSMenuItem(title: "Visit patcha.app", action: #selector(visitWebsite), keyEquivalent: "")
        visitItem.target = self
        sub.addItem(visitItem)
        let item = NSMenuItem(title: "Resources", action: nil, keyEquivalent: "")
        item.submenu = sub
        return item
    }

    private func updateMenuState() {
        updateIcon(for: daemonManager.status)
        updateStatusLabel(for: daemonManager.status, pausedUntil: daemonManager.pausedUntil)
        resumeNowItem?.isHidden = daemonManager.status != .paused
    }

    private func refreshPermissions() {
        grantAccessibilityItem.isHidden = PermissionsManager.accessibilityGranted()

        let srGranted = PermissionsManager.screenRecordingGranted()
        let srOpened = UserDefaults.standard.bool(forKey: "didOpenScreenRecordingPrefs")
        grantScreenRecordingItem.isHidden = srGranted || srOpened
    }

    @objc private func appDidBecomeActive() {
        refreshPermissions()
        if case .stopped = daemonManager.status, PermissionsManager.accessibilityGranted() {
            daemonManager.start()
        }
        updateMenuState()
    }

    private func updateIcon(for daemonStatus: DaemonStatus) {
        guard let button = statusItem.button else { return }

        let iconName: String
        switch daemonStatus {
        case .running:                        iconName = "icon-recording"
        case .paused:                         iconName = "icon-paused"
        case .failed:                         iconName = "icon-error"
        case .stopped, .starting, .restarting: iconName = "icon-starting"
        }

        if let url = Bundle.main.url(forResource: iconName, withExtension: "svg"),
           let image = NSImage(contentsOf: url) {
            image.size = NSSize(width: 18, height: 18)
            image.isTemplate = true
            button.image = image
            button.alphaValue = 1.0
        } else if let fallback = bundledIcon() {
            button.image = fallback
            button.alphaValue = daemonStatus == .running ? 1.0 : 0.5
        } else {
            button.title = "P"
        }
    }

    private func bundledIcon() -> NSImage? {
        guard let url = Bundle.main.url(forResource: "menubar-icon", withExtension: "svg"),
              let image = NSImage(contentsOf: url) else { return nil }
        image.size = NSSize(width: 18, height: 18)
        image.isTemplate = true
        return image
    }

    private func updateStatusLabel(for daemonStatus: DaemonStatus, pausedUntil: Date?) {
        switch daemonStatus {
        case .stopped, .starting:
            statusMenuItem.title = "Starting up..."
        case .running:
            statusMenuItem.title = "Patcha is watching..."
        case .paused:
            if let until = pausedUntil {
                let remaining = max(0, Int(until.timeIntervalSinceNow))
                statusMenuItem.title = "Paused · resumes in \(formatDuration(remaining))"
            } else {
                statusMenuItem.title = "Paused"
            }
        case .restarting(let attempt):
            statusMenuItem.title = "Restarting... (attempt \(attempt))"
        case .failed:
            statusMenuItem.title = "Daemon stopped unexpectedly"
        }
    }

    private func formatDuration(_ seconds: Int) -> String {
        if seconds <= 0 { return "now" }
        if seconds < 3600 { return "\(seconds / 60)m" }
        let h = seconds / 3600
        let m = (seconds % 3600) / 60
        return m > 0 ? "\(h)h \(m)m" : "\(h)h"
    }

    private func nextMidnight() -> Date {
        var comps = Calendar.current.dateComponents([.year, .month, .day], from: Date())
        comps.day = (comps.day ?? 0) + 1
        comps.hour = 0; comps.minute = 0; comps.second = 0
        return Calendar.current.date(from: comps) ?? Date().addingTimeInterval(86400)
    }

    @objc private func pauseFor(_ sender: NSMenuItem) {
        let secs = TimeInterval(sender.tag)
        daemonManager.pause(until: Date().addingTimeInterval(secs))
    }

    @objc private func pauseUntilTomorrow() {
        daemonManager.pause(until: nextMidnight())
    }

    @objc private func resumeNow() {
        daemonManager.resume()
    }

    @objc private func openSettings() {
        settingsWindowController.show()
    }

    @objc private func restartDaemon() {
        daemonManager.restart()
    }

    @objc private func visitWebsite() {
        NSWorkspace.shared.open(URL(string: "https://patcha.app")!)
    }

    @objc private func openAccessibilityPrefs() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
    }

    @objc private func openScreenRecordingPrefs() {
        UserDefaults.standard.set(true, forKey: "didOpenScreenRecordingPrefs")
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!)
    }

    @objc private func quitApp() {
        (NSApp.delegate as? AppDelegate)?.quit()
    }
}

extension MenuBarController: NSMenuDelegate {
    func menuWillOpen(_ menu: NSMenu) {
        refreshPermissions()
        resumeNowItem?.isHidden = daemonManager.status != .paused
        updateStatusLabel(for: daemonManager.status, pausedUntil: daemonManager.pausedUntil)
    }
}
