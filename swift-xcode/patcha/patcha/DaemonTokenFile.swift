import Foundation

/// Shared access-token file the Python daemon re-reads on a 401.
///
/// The macOS app owns the Supabase refresh token and keeps this file current.
/// The daemon only ever reads the access token from here, so it can pick up a
/// rotated token without being restarted, and can never trigger refresh-token
/// rotation conflicts with the app.
enum DaemonTokenFile {
    static let url = FileManager.default
        .homeDirectoryForCurrentUser
        .appendingPathComponent(".patcha")
        .appendingPathComponent("access_token")

    static func write(_ token: String?) {
        guard let token, !token.isEmpty else {
            clear()
            return
        }
        do {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try Data(token.utf8).write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        } catch {
            NSLog("[DaemonTokenFile] failed to write token: %@", error.localizedDescription)
        }
    }

    static func clear() {
        try? FileManager.default.removeItem(at: url)
    }
}
