import AppKit
import Combine
import Supabase

class AppDelegate: NSObject, NSApplicationDelegate {
    var menuBarController: MenuBarController!
    var daemonManager: DaemonManager!
    var mcpManager: MCPManager!
    var settingsWindowController: SettingsWindowController!
    var authManager: AuthManager!

    private var isQuitting = false
    private var cancellables: Set<AnyCancellable> = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let store = SettingsStore()
        authManager = AuthManager()
        daemonManager = DaemonManager()
        mcpManager = MCPManager()
        mcpManager.start()
        settingsWindowController = SettingsWindowController(daemonManager: daemonManager, settingsStore: store, authManager: authManager, mcpManager: mcpManager)
        menuBarController = MenuBarController(daemonManager: daemonManager, mcpManager: mcpManager, settingsWindowController: settingsWindowController, settingsStore: store)
        Installer.installIfNeeded()

        authManager.$initialSessionLoaded
            .filter { $0 }
            .first()
            .sink { [weak self] _ in
                Task { @MainActor [weak self] in self?.handleLaunchAuthState() }
            }
            .store(in: &cancellables)

        authManager.$isSignedIn
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] signedIn in
                Task { @MainActor [weak self] in
                    self?.handleAuthState(signedIn: signedIn)
                }
            }
            .store(in: &cancellables)

        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            guard let self else { return }
            if !self.authManager.initialSessionLoaded {
                self.settingsWindowController.show()
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

    @MainActor private func handleLaunchAuthState() {
        if authManager.isSignedIn {
            handleSignedIn()
        } else {
            settingsWindowController.show()
        }
    }

    @MainActor private func handleAuthState(signedIn: Bool) {
        if signedIn {
            handleSignedIn()
        } else {
            if case .running = daemonManager.status { daemonManager.stop() }
            mcpManager.stop()
            showLogin()
        }
    }

    @MainActor private func handleSignedIn() {
        daemonManager.authToken = authManager.session?.accessToken
        mcpManager.authToken = authManager.session?.accessToken
        if case .stopped = daemonManager.status {
            daemonManager.start()
        }
        mcpManager.start()
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
        mcpManager.stop()
    }
}
