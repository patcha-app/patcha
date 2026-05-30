import SwiftUI
import Supabase

struct AccountPane: View {
    @ObservedObject var authManager: AuthManager
    @State private var isSigningOut = false
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                profileSection
                if let error = errorMessage {
                    Text(error)
                        .font(.britanica(13))
                        .foregroundStyle(.red)
                }
                signOutSection
            }
            .padding(20)
        }
        .scrollIndicators(.hidden)
        .background(ScrollerHider())
    }

    private var profileSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Account")

            HStack(spacing: 12) {
                AsyncImage(url: authManager.avatarURL) { image in
                    image.resizable().scaledToFill()
                } placeholder: {
                    Image(systemName: "person.circle.fill")
                        .font(.britanica(36))
                        .foregroundStyle(.secondary)
                }
                .frame(width: 40, height: 40)
                .clipShape(Circle())

                VStack(alignment: .leading, spacing: 2) {
                    if let name = authManager.session?.user.userMetadata["full_name"]?.stringValue {
                        Text(name)
                            .font(.britanica(13))
                    }
                    Text(authManager.session?.user.email ?? "—")
                        .font(.britanica(13))
                        .foregroundStyle(.secondary)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color.primary.opacity(0.05))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(PatchaTheme.softDivider, lineWidth: 1)
            )
        }
    }

    private var signOutSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Session")

            HStack {
                Text("Signed in")
                    .font(.britanica(13))
                Spacer()
                Button(isSigningOut ? "Signing out…" : "Sign Out") {
                    isSigningOut = true
                    errorMessage = nil
                    Task {
                        defer { isSigningOut = false }
                        do {
                            try await authManager.signOut()
                        } catch {
                            errorMessage = error.localizedDescription
                        }
                    }
                }
                .buttonStyle(AccentButtonStyle())
                .disabled(isSigningOut)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 11)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color.primary.opacity(0.05))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(PatchaTheme.softDivider, lineWidth: 1)
            )
        }
    }
}
