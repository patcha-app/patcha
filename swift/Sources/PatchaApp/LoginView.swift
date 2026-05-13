import SwiftUI
import AppKit

struct LoginView: View {
    @ObservedObject var authManager: AuthManager
    @State private var email = ""
    @State private var password = ""
    @State private var isSignUp = false
    @State private var errorMessage: String?
    @State private var isLoading = false

    var body: some View {
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
                    .textFieldStyle(.roundedBorder)

                SecureField("Password", text: $password)
                    .textFieldStyle(.roundedBorder)
            }

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
                .buttonStyle(.borderedProminent)
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

            Divider()

            Button("Continue with Google") {
                do {
                    try authManager.signInWithGoogle()
                } catch {
                    errorMessage = error.localizedDescription
                }
            }
            .buttonStyle(.bordered)
            .frame(maxWidth: .infinity)
        }
        .padding(32)
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
