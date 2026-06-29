import SwiftUI


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
        case .timeline:
            TimelinePane(mcpManager: mcpManager)
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
            } else if !store.onboardingCompleted {
                OnboardingView(store: store)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                HStack(spacing: 0) {
                    VStack(spacing: 0) {
                        AccessibilityBanner()
                        SettingsSidebar(selected: $section, authManager: authManager)
                    }
                    .frame(width: 220)
                    .ignoresSafeArea(edges: .top)

                    contentView
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(PatchaTheme.surface(for: colorScheme))
                        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .stroke(PatchaTheme.hairline(for: colorScheme), lineWidth: 1)
                        )
                        .padding([.top, .trailing, .bottom], 10)
                        .ignoresSafeArea(edges: .top)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(PatchaTheme.bg(for: colorScheme), ignoresSafeAreaEdges: .all)
                .onAppear { permissionsVM.scan() }
            }
        }
        .tint(PatchaTheme.accent)
        .accentColor(PatchaTheme.accent)
    }
}
