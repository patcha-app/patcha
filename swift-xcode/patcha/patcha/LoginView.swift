import SwiftUI
import AppKit

// MARK: - LoginView

struct LoginView: View {
    @ObservedObject var authManager: AuthManager
    @Environment(\.colorScheme) private var colorScheme
    @State private var email = ""
    @State private var password = ""
    @State private var isSignUp = false
    @State private var errorMessage: String?
    @State private var isLoading = false

    private var isFormDisabled: Bool {
        email.isEmpty || password.isEmpty || isLoading
    }

    var body: some View {
        GeometryReader { geo in
            HStack(spacing: 0) {
                leftPane
                    .frame(width: geo.size.width / 2, height: geo.size.height)
                    .background(PatchaTheme.bg(for: colorScheme))

                rightPane
                    .frame(width: geo.size.width / 2, height: geo.size.height)
            }
        }
        .ignoresSafeArea()
        .disabled(isLoading)
    }
}

// MARK: - Subviews

private extension LoginView {
    var leftPane: some View {
        ZStack(alignment: .topLeading) {
            PatchaTheme.bg(for: colorScheme)
            formContent
        }
    }

    var formContent: some View {
        VStack(alignment: .leading, spacing: 20) {
            header
            fields
            if let error = errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            actions
            toggleModeButton
        }
        .frame(maxWidth: 320)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
        .padding(.horizontal, 36)
    }

    var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(isSignUp ? "Create your account" : "Welcome back")
                .font(.system(size: 22, weight: .semibold))
                .foregroundStyle(.primary)
            Text(isSignUp ? "Start tracking your activity in seconds." : "Sign in to continue to Patcha.")
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
        }
    }

    var fields: some View {
        VStack(spacing: 10) {
            LoginField(title: "Email", text: $email, isSecure: false)
            LoginField(title: "Password", text: $password, isSecure: true)
        }
    }

    var actions: some View {
        VStack(spacing: 10) {
            Button(isSignUp ? "Create Account" : "Sign In") { submit() }
                .buttonStyle(AccentButtonStyle(isDisabled: isFormDisabled))
                .disabled(isFormDisabled)

            divider

            Button {
                do { try authManager.signInWithGoogle() }
                catch { errorMessage = error.localizedDescription }
            } label: {
                HStack(spacing: 10) {
                    GoogleGLogo()
                    Text("Continue with Google")
                        .font(.system(size: 13, weight: .medium))
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 9)
            }
            .buttonStyle(GoogleButtonStyle())
        }
    }

    var divider: some View {
        HStack(spacing: 10) {
            Rectangle().fill(PatchaTheme.softDivider).frame(height: 1)
            Text("or").font(.caption2).foregroundStyle(.secondary)
            Rectangle().fill(PatchaTheme.softDivider).frame(height: 1)
        }
        .padding(.vertical, 2)
    }

    var toggleModeButton: some View {
        Button {
            isSignUp.toggle()
            errorMessage = nil
        } label: {
            HStack(spacing: 4) {
                Text(isSignUp ? "Already have an account?" : "Don't have an account?")
                    .foregroundStyle(.secondary)
                Text(isSignUp ? "Sign In" : "Sign Up")
                    .foregroundStyle(PatchaTheme.accent)
                    .fontWeight(.medium)
            }
            .font(.caption)
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity)
        .padding(.top, 4)
    }

    var rightPane: some View {
        ZStack {
            if let url = Bundle.main.url(forResource: "login-bg", withExtension: "png", subdirectory: LoginView.assetsSubdirectory),
               let nsImage = NSImage(contentsOf: url) {
                Image(nsImage: nsImage)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
            } else {
                LinearGradient(
                    colors: [
                        PatchaTheme.accent.opacity(colorScheme == .dark ? 0.35 : 0.22),
                        PatchaTheme.accent.opacity(colorScheme == .dark ? 0.15 : 0.08)
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                Text("Patcha")
                    .font(.system(size: 56, weight: .bold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.18))
            }
        }
        .clipped()
    }
}

// MARK: - Actions

private extension LoginView {
    static let assetsSubdirectory = "AppIcon.icon/Assets"

    func submit() {
        errorMessage = nil
        isLoading = true
        Task {
            defer { isLoading = false }
            do {
                if isSignUp {
                    let hasSession = try await authManager.signUp(email: email, password: password)
                    if !hasSession {
                        errorMessage = "Check your email to confirm your account."
                    }
                } else {
                    try await authManager.signIn(email: email, password: password)
                }
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}

// MARK: - Supporting Views

private struct LoginField: View {
    let title: String
    @Binding var text: String
    let isSecure: Bool
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)

            Group {
                if isSecure {
                    SecureField("", text: $text)
                } else {
                    TextField("", text: $text)
                }
            }
            .textFieldStyle(.plain)
            .font(.system(size: 13))
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(Color.primary.opacity(colorScheme == .dark ? 0.08 : 0.04))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(Color.primary.opacity(colorScheme == .dark ? 0.12 : 0.10), lineWidth: 1)
            )
        }
    }
}

private struct GoogleGLogo: View {
    var body: some View {
        if let url = Bundle.main.url(forResource: "google", withExtension: "svg", subdirectory: LoginView.assetsSubdirectory),
           let image = NSImage(contentsOf: url) {
            Image(nsImage: image)
                .resizable()
                .scaledToFit()
                .frame(width: 18, height: 18)
                .accessibilityHidden(true)
        }
    }
}

// MARK: - Button Styles

private struct AccentButtonStyle: ButtonStyle {
    let isDisabled: Bool
    @Environment(\.colorScheme) private var colorScheme

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .foregroundColor(PatchaTheme.bg(for: colorScheme))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(PatchaTheme.accent.opacity(isDisabled ? 0.4 : configuration.isPressed ? 0.8 : 1.0))
            )
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

private struct GoogleButtonStyle: ButtonStyle {
    @Environment(\.colorScheme) private var colorScheme

    func makeBody(configuration: Configuration) -> some View {
        let bg: Color = colorScheme == .dark
            ? Color.white.opacity(configuration.isPressed ? 0.10 : 0.06)
            : Color.white
        let border = Color.primary.opacity(colorScheme == .dark ? 0.18 : 0.15)
        let fg: Color = colorScheme == .dark ? .white : .black

        return configuration.label
            .foregroundStyle(fg)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous).fill(bg)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(border, lineWidth: 1)
            )
            .opacity(configuration.isPressed ? 0.85 : 1.0)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}
