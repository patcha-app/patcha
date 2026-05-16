import SwiftUI
import AppKit

private let bgLight = Color(hex: "#F4F4F5")
private let bgDark = Color(hex: "#1A1A1A")
private let accentDark = Color(hex: "#CDF064")
private let accentLight = PatchaTheme.lightAccent

struct LoginView: View {
    @ObservedObject var authManager: AuthManager
    @Environment(\.colorScheme) private var colorScheme
    @State private var email = ""
    @State private var password = ""
    @State private var isSignUp = false
    @State private var errorMessage: String?
    @State private var isLoading = false

    private var bgColor: Color {
        colorScheme == .dark ? bgDark : bgLight
    }

    var body: some View {
        ZStack {
            bgColor

            VStack(spacing: 24) {
                if let icon = NSApp.applicationIconImage {
                    Image(nsImage: icon)
                        .resizable()
                        .frame(width: 64, height: 64)
                }

                Text(isSignUp ? "Create Account" : "Sign in to Patcha")
                    .font(.title2)
                    .fontWeight(.semibold)

                VStack(spacing: 10) {
                    TextField("Email", text: $email)
                        .textFieldStyle(.plain)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 9)
                        .background(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .fill(Color.primary.opacity(0.06))
                        )

                    SecureField("Password", text: $password)
                        .textFieldStyle(.plain)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 9)
                        .background(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .fill(Color.primary.opacity(0.06))
                        )
                }
                .padding(16)
                .background(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .fill(Color.primary.opacity(0.03))
                )

                if let error = errorMessage {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                }

                VStack(spacing: 8) {
                    Button(isSignUp ? "Create Account" : "Sign In") {
                        submit()
                    }
                    .buttonStyle(AccentButtonStyle(isDisabled: email.isEmpty || password.isEmpty || isLoading))
                    .disabled(email.isEmpty || password.isEmpty || isLoading)
                    .frame(maxWidth: .infinity)

                    Button(isSignUp ? "Already have an account? Sign In" : "Don't have an account? Sign Up") {
                        isSignUp.toggle()
                        errorMessage = nil
                    }
                    .buttonStyle(.plain)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }

                HStack(spacing: 12) {
                    Rectangle()
                        .fill(PatchaTheme.softDivider)
                        .frame(height: 1)
                    Text("or")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Rectangle()
                        .fill(PatchaTheme.softDivider)
                        .frame(height: 1)
                }

                Button("Continue with Google") {
                    do {
                        try authManager.signInWithGoogle()
                    } catch {
                        errorMessage = error.localizedDescription
                    }
                }
                .buttonStyle(AccentButtonStyle(isDisabled: false))
                .frame(maxWidth: .infinity)
            }
            .padding(32)
        }
        .ignoresSafeArea()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .disabled(isLoading)
    }

    private func submit() {
        errorMessage = nil
        isLoading = true
        Task {
            defer { isLoading = false }
            do {
                if isSignUp {
                    try await authManager.signUp(email: email, password: password)
                } else {
                    try await authManager.signIn(email: email, password: password)
                }
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}

private struct AccentButtonStyle: ButtonStyle {
    let isDisabled: Bool
    @Environment(\.colorScheme) private var colorScheme

    func makeBody(configuration: Configuration) -> some View {
        let accent = colorScheme == .dark ? accentDark : accentLight
        configuration.label
            .font(.body.weight(.medium))
            .foregroundColor(.black)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(accent.opacity(isDisabled ? 0.4 : configuration.isPressed ? 0.8 : 1.0))
            )
    }
}
