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

    private var isQuitting = false
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
            DaemonTokenFile.clear()
            showLogin()
        }
    }

    @MainActor private func handleSignedIn() {
        Task {
            // Refresh the session before starting the daemon so we never launch
            // with a stale token that was restored from the keychain at app start.
            let token: String?
            do {
                token = try await supabase.auth.session.accessToken
            } catch {
                token = authManager.session?.accessToken
            }
            daemonManager.authToken = token
            mcpManager.authToken = token
            DaemonTokenFile.write(token)
            if case .stopped = daemonManager.status {
                daemonManager.start()
            }
            mcpManager.start()
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
        mcpManager.stop()
    }

    private func registerFonts() {
        let fontNames = [
            "Britanica Black Italic", "Britanica Black",
            "Britanica Bold Italic", "Britanica Bold",
            "Britanica Condensed Bold Italic", "Britanica Condensed Bold",
            "Britanica Condensed Extra Bold",
            "Britanica Condensed Light Italic", "Britanica Condensed Light",
            "Britanica Condensed Regular Italic", "Britanica Condensed Regular",
            "Britanica Condensed Thin Italic", "Britanica Condensed Thin",
            "Britanica Expanded Black Italic", "Britanica Expanded Black",
            "Britanica Expanded Bold Italic", "Britanica Expanded Bold",
            "Britanica Expanded Extra Bold Italic", "Britanica Expanded Extra Bold",
            "Britanica Expanded Heavy Italic", "Britanica Expanded Heavy",
            "Britanica Expanded Light Italic", "Britanica Expanded Light",
            "Britanica Expanded Regular Italic", "Britanica Expanded Regular",
            "Britanica Expanded Thin Italic", "Britanica Expanded Thin",
            "Britanica Extra Bold Italic", "Britanica Extra Bold",
            "Britanica Heavy Italic", "Britanica Heavy",
            "Britanica Light Italic", "Britanica Light",
            "Britanica Regular Italic", "Britanica Regular",
            "Britanica Semi Expanded Black Italic", "Britanica Semi Expanded Black",
            "Britanica Semi Expanded Bold Italic", "Britanica Semi Expanded Bold",
            "Britanica Semi Expanded Extra Bold Italic", "Britanica Semi Expanded Extra Bold",
            "Britanica Semi Expanded Heavy Italic", "Britanica Semi Expanded Heavy",
            "Britanica Semi Expanded Light Italic", "Britanica Semi Expanded Light",
            "Britanica Semi Expanded Regular Italic", "Britanica Semi Expanded Regular",
            "Britanica Semi Expanded Thin Italic", "Britanica Semi Expanded Thin",
            "Britanica Thin Italic", "Britanica Thin",
            "InstrumentSerif-Regular", "InstrumentSerif-Italic",
        ]
        for name in fontNames {
            guard let url = Bundle.main.url(forResource: name, withExtension: "ttf") else { continue }
            CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        }
    }
}
