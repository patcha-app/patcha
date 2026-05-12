import AppKit

class AppDelegate: NSObject, NSApplicationDelegate {
    var menuBarController: MenuBarController!
    var daemonManager: DaemonManager!
    var settingsWindowController: SettingsWindowController!

    private var isQuitting = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let store = SettingsStore()
        daemonManager = DaemonManager()
        settingsWindowController = SettingsWindowController(daemonManager: daemonManager, settingsStore: store)
        menuBarController = MenuBarController(daemonManager: daemonManager, settingsWindowController: settingsWindowController)
        Installer.installIfNeeded()
        PermissionsManager.requestIfNeeded()
        daemonManager.start()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        isQuitting ? .terminateNow : .terminateCancel
    }

    func quit() {
        isQuitting = true
        NSApp.terminate(nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        daemonManager.stop()
    }
}
