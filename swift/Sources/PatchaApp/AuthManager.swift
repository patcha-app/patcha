import Foundation
import AppKit
import Supabase
import Combine

@MainActor
final class AuthManager: ObservableObject {
    @Published var session: Session? = nil
    @Published var isSignedIn: Bool = false
    @Published var initialSessionLoaded: Bool = false

    init() {
        Task {
            for await (event, session) in supabase.auth.authStateChanges {
                switch event {
                case .initialSession, .signedIn, .tokenRefreshed, .userUpdated:
                    self.session = session
                    self.isSignedIn = session != nil
                case .signedOut, .userDeleted:
                    self.session = nil
                    self.isSignedIn = false
                default:
                    break
                }
                if !self.initialSessionLoaded {
                    self.initialSessionLoaded = true
                }
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

