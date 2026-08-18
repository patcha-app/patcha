import AppKit
import Combine
import CoreText
import Supabase

class AppDelegate: NSObject, NSApplicationDelegate {
    var menuBarController: MenuBarController!
    var daemonManager: DaemonManager!
    var mcpManager: MCPManager!
    var settingsWindowController: SettingsWindowController!
    var authManager: AuthManager!

    private var cancellables: Set<AnyCancellable> = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        registerFonts()
        NSApp.setActivationPolicy(.accessory)
        let store = SettingsStore()
        authManager = AuthManager()
        daemonManager = DaemonManager()
        mcpManager = MCPManager()
        mcpManager.start()
        settingsWindowController = SettingsWindowController(daemonManager: daemonManager, settingsStore: store, authManager: authManager, mcpManager: mcpManager)
        menuBarController = MenuBarController(daemonManager: daemonManager, mcpManager: mcpManager, settingsWindowController: settingsWindowController, settingsStore: store)
        Installer.installIfNeeded()

        // Login is optional: the daemon runs fully offline and uses the local
        // Claude CLI for AI features when there is no token. A token, when
        // present, upgrades those features to patcha-api — but its absence never
        // stops the daemon. Clear it on sign-out so the daemon falls back cleanly.
        authManager.$isSignedIn
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] signedIn in
                Task { @MainActor [weak self] in
                    guard let self, !signedIn else { return }
                    DaemonTokenFile.clear()
                    self.daemonManager.authToken = nil
                    self.mcpManager.authToken = nil
                }
            }
            .store(in: &cancellables)

        authManager.$session
            .dropFirst()
            .map { $0?.accessToken }
            .removeDuplicates()
            .compactMap { $0 }
            .sink { [weak self] token in
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    self.daemonManager.authToken = token
                    self.mcpManager.authToken = token
                    // The daemon re-reads the token file on its next 401, so a
                    // rotation no longer requires restarting the process.
                    DaemonTokenFile.write(token)
                }
            }
            .store(in: &cancellables)

        // Start the daemon + MCP immediately, independent of any auth state or
        // network. The menu bar can open Settings to sign in at any time.
        startServices()
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        NSLog("[AppDelegate] application(_:open:) called with %d URL(s)", urls.count)
        for url in urls {
            NSLog("[AppDelegate] handling URL: %@", url.absoluteString)
            authManager.handleURL(url)
        }
    }

    @MainActor private func startServices() {
        // If a token was restored from the keychain, hand it to the daemon so it
        // can use patcha-api; otherwise the daemon starts token-less and uses the
        // local Claude CLI. Either way the daemon and MCP server start now.
        if let token = authManager.session?.accessToken {
            daemonManager.authToken = token
            mcpManager.authToken = token
            DaemonTokenFile.write(token)
        }
        if case .stopped = daemonManager.status {
            daemonManager.start()
        }
        mcpManager.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        daemonManager.stop()
        mcpManager.stop()
    }

    private func registerFonts() {
        let fontNames = [
            "Britanica-Regular",
            "Britanica-Bold",
            "Britanica-Semi-Expanded-Bold",
            "Britanica-Semi-Expanded-Extra-Bold",
            "InstrumentSerif-Regular",
            "InstrumentSerif-Italic",
        ]
        for name in fontNames {
            guard let url = Bundle.main.url(forResource: name, withExtension: "ttf") else { continue }
            CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        }
    }
}
