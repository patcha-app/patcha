import AppKit
import Combine

class AppDelegate: NSObject, NSApplicationDelegate {
    var menuBarController: MenuBarController!
    var daemonManager: DaemonManager!
    var settingsWindowController: SettingsWindowController!
    var authManager: AuthManager!

    private var isQuitting = false
    private var authCancellable: AnyCancellable?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let store = SettingsStore()
        authManager = AuthManager()
        daemonManager = DaemonManager()
        settingsWindowController = SettingsWindowController(daemonManager: daemonManager, settingsStore: store, authManager: authManager)
        menuBarController = MenuBarController(daemonManager: daemonManager, settingsWindowController: settingsWindowController)
        Installer.installIfNeeded()
        PermissionsManager.requestIfNeeded()

        authCancellable = authManager.$isSignedIn
            .dropFirst()
            .sink { [weak self] signedIn in
                Task { @MainActor [weak self] in
                    self?.handleAuthState(signedIn: signedIn)
                }
            }
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        NSLog("[AppDelegate] application(_:open:) called with %d URL(s)", urls.count)
        for url in urls {
            NSLog("[AppDelegate] handling URL: %@", url.absoluteString)
            authManager.handleURL(url)
        }
    }

    @MainActor private func handleAuthState(signedIn: Bool) {
        if signedIn {
            daemonManager.authToken = authManager.session?.accessToken
            if case .stopped = daemonManager.status {
                daemonManager.start()
            }
        } else {
            if case .running = daemonManager.status { daemonManager.stop() }
            showLogin()
        }
    }

    private func showLogin() {
        settingsWindowController.show()
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
