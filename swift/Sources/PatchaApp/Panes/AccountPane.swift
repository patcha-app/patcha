import SwiftUI

struct AccountPane: View {
    @ObservedObject var authManager: AuthManager
    @State private var isSigningOut = false
    @State private var errorMessage: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "person.circle")
                    .font(.title2)
                Text("Account")
                    .font(.title2)
                    .fontWeight(.semibold)
            }
            .padding(.horizontal, 20)
            .padding(.top, 32)
            .padding(.bottom, 16)

            Form {
                Section("Signed In As") {
                    HStack(spacing: 12) {
                        AsyncImage(url: authManager.avatarURL) { image in
                            image.resizable().scaledToFill()
                        } placeholder: {
                            Image(systemName: "person.circle.fill")
                                .font(.system(size: 36))
                                .foregroundStyle(.secondary)
                        }
                        .frame(width: 40, height: 40)
                        .clipShape(Circle())

                        VStack(alignment: .leading, spacing: 2) {
                            if let name = authManager.session?.user.userMetadata["full_name"]?.stringValue {
                                Text(name).fontWeight(.medium)
                            }
                            Text(authManager.session?.user.email ?? "—")
                                .foregroundStyle(.secondary)
                                .font(.callout)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
            .formStyle(.grouped)
            .scrollContentBackground(.hidden)

            if let error = errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(.horizontal, 20)
            }

            Spacer()

            HStack {
                Spacer()
                Button("Sign Out") {
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
                .buttonStyle(.bordered)
                .disabled(isSigningOut)
            }
            .padding()
        }
    }
}
