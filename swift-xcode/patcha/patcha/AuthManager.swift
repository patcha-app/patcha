import Foundation
import AppKit
import Supabase
import Combine

@MainActor
final class AuthManager: ObservableObject {
    @Published var session: Session? = nil
    @Published var isSignedIn: Bool = false
    @Published var initialSessionLoaded: Bool = false

    private var refreshTimer: Timer?
    private var refreshFailureCount = 0
    private let maxRefreshFailures = 3
    private let refreshCheckInterval: TimeInterval = 60
    private let refreshMargin: TimeInterval = 300

    init() {
        Task {
            for await (event, session) in supabase.auth.authStateChanges {
                switch event {
                case .initialSession, .signedIn, .tokenRefreshed, .userUpdated:
                    self.session = session
                    self.isSignedIn = session != nil
                    if session != nil {
                        self.refreshFailureCount = 0
                        self.startTokenRefresh()
                    }
                case .signedOut, .userDeleted:
                    self.session = nil
                    self.isSignedIn = false
                    self.stopTokenRefresh()
                default:
                    break
                }
                if !self.initialSessionLoaded {
                    self.initialSessionLoaded = true
                }
            }
        }
    }

    private func startTokenRefresh() {
        guard refreshTimer == nil else { return }
        let timer = Timer(timeInterval: refreshCheckInterval, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in await self?.refreshIfNeeded() }
        }
        RunLoop.main.add(timer, forMode: .common)
        refreshTimer = timer
        Task { await refreshIfNeeded() }
    }

    private func stopTokenRefresh() {
        refreshTimer?.invalidate()
        refreshTimer = nil
        refreshFailureCount = 0
    }

    /// Refreshes the access token before it expires. Retries transient (network)
    /// failures indefinitely; after `maxRefreshFailures` genuine auth failures the
    /// refresh token is treated as dead and the user is signed out.
    private func refreshIfNeeded() async {
        guard let current = supabase.auth.currentSession else { return }
        let secondsUntilExpiry = current.expiresAt - Date().timeIntervalSince1970
        guard secondsUntilExpiry <= refreshMargin else { return }

        do {
            _ = try await supabase.auth.refreshSession()
            refreshFailureCount = 0
        } catch {
            let nsError = error as NSError
            if error is URLError || nsError.domain == NSURLErrorDomain {
                NSLog("[AuthManager] token refresh network error (will retry): %@", error.localizedDescription)
                return
            }
            refreshFailureCount += 1
            NSLog("[AuthManager] token refresh failed (%d/%d): %@", refreshFailureCount, maxRefreshFailures, error.localizedDescription)
            if refreshFailureCount >= maxRefreshFailures {
                NSLog("[AuthManager] refresh token rejected %d times — signing out", maxRefreshFailures)
                stopTokenRefresh()
                try? await supabase.auth.signOut()
            }
        }
    }

    func signIn(email: String, password: String) async throws {
        try await supabase.auth.signIn(email: email, password: password)
    }

    func signUp(email: String, password: String) async throws -> Bool {
        let response = try await supabase.auth.signUp(email: email, password: password)
        return response.session != nil
    }

    func signInWithGoogle() throws {
        let url = try supabase.auth.getOAuthSignInURL(
            provider: .google,
            redirectTo: URL(string: PatchaConfig.authCallbackURL)!
        )
        NSWorkspace.shared.open(url)
    }

    func signOut() async throws {
        try await supabase.auth.signOut()
    }

    var avatarURL: URL? {
        guard let urlString = session?.user.userMetadata["avatar_url"]?.stringValue
                           ?? session?.user.userMetadata["picture"]?.stringValue else { return nil }
        return URL(string: urlString)
    }

    func handleURL(_ url: URL) {
        Task {
            do {
                try await supabase.auth.session(from: url)
                NSLog("[AuthManager] session(from:) succeeded")
            } catch {
                NSLog("[AuthManager] session(from:) failed: %@", error.localizedDescription)
            }
        }
    }
}

