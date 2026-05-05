import AppKit

class AppDelegate: NSObject, NSApplicationDelegate {
    var menuBarController: MenuBarController!
    var daemonManager: DaemonManager!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        daemonManager = DaemonManager()
        menuBarController = MenuBarController(daemonManager: daemonManager)
        PermissionsManager.requestIfNeeded()
        daemonManager.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        daemonManager.stop()
    }
}
