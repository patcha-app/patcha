import AppKit

class AppDelegate: NSObject, NSApplicationDelegate {
    var menuBarController: MenuBarController!
    var daemonManager: DaemonManager!
    var settingsWindowController: SettingsWindowController!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        daemonManager = DaemonManager()
        settingsWindowController = SettingsWindowController(daemonManager: daemonManager)
        menuBarController = MenuBarController(daemonManager: daemonManager, settingsWindowController: settingsWindowController)
        PermissionsManager.requestIfNeeded()
        daemonManager.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        daemonManager.stop()
    }
}
