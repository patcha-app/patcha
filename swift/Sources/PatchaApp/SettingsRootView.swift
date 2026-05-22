import SwiftUI

@MainActor
private struct SettingsRootView_Previews: PreviewProvider {
    static var signedInAuth: AuthManager {
        let auth = AuthManager()
        auth.isSignedIn = true
        return auth
    }

    static var previews: some View {
        Group {
            SettingsRootView(
                store: SettingsStore(),
                daemonManager: nil,
                authManager: AuthManager(),
                mcpManager: MCPManager()
            )
            .frame(width: 700, height: 500)
            .previewDisplayName("Login")

            SettingsRootView(
                store: SettingsStore(),
                daemonManager: nil,
                authManager: signedInAuth,
                mcpManager: MCPManager()
            )
            .frame(width: 700, height: 500)
            .previewDisplayName("Settings")
        }
    }
}

struct SettingsRootView: View {
    @ObservedObject var store: SettingsStore
    @ObservedObject var authManager: AuthManager
    @ObservedObject var mcpManager: MCPManager
    var daemonManager: DaemonManager?
    @State private var section: SettingsSection = .permissions
    @StateObject private var permissionsVM: AppPermissionsViewModel
    @Environment(\.colorScheme) private var colorScheme

    init(store: SettingsStore, daemonManager: DaemonManager?, authManager: AuthManager, mcpManager: MCPManager) {
        self.store = store
        self.daemonManager = daemonManager
        self.authManager = authManager
        self.mcpManager = mcpManager
        self._permissionsVM = StateObject(wrappedValue: AppPermissionsViewModel(store: store))
    }

    @ViewBuilder
    private var contentView: some View {
        switch section {
        case .permissions:
            AppPermissionsPane(store: store, viewModel: permissionsVM)
        case .general:
            if let dm = daemonManager {
                GeneralPane(store: store, daemonManager: dm)
            } else {
                PlaceholderPane(section: .general)
            }
        case .memories:
            PlaceholderPane(section: .memories)
        case .modelPreference:
            PlaceholderPane(section: .modelPreference)
        case .integrations:
            IntegrationsPane(mcpManager: mcpManager)
        case .account:
            AccountPane(authManager: authManager)
        }
    }

    var body: some View {
        Group {
            if !authManager.isSignedIn {
                LoginView(authManager: authManager)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                HStack(spacing: 0) {
                    SettingsSidebar(selected: $section)
                        .frame(width: 220)
                        .background(PatchaTheme.bg(for: colorScheme).ignoresSafeArea())
                        .ignoresSafeArea(edges: .top)
                    contentView
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(PatchaTheme.bg(for: colorScheme).ignoresSafeArea())
                        .ignoresSafeArea(edges: .top)
                }
                .frame(width: 700)
                .frame(maxHeight: .infinity)
                .onAppear { permissionsVM.scan() }
            }
        }
        .tint(PatchaTheme.accent)
        .accentColor(PatchaTheme.accent)
    }
}
