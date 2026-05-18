import SwiftUI

extension Color {
    init(hex: String) {
        let cleaned = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        var int: UInt64 = 0
        Scanner(string: cleaned).scanHexInt64(&int)
        let r, g, b, a: UInt64
        switch cleaned.count {
        case 6:
            (r, g, b, a) = ((int >> 16) & 0xFF, (int >> 8) & 0xFF, int & 0xFF, 255)
        case 8:
            (r, g, b, a) = ((int >> 24) & 0xFF, (int >> 16) & 0xFF, (int >> 8) & 0xFF, int & 0xFF)
        default:
            (r, g, b, a) = (255, 255, 255, 255)
        }
        self.init(
            .sRGB,
            red: Double(r) / 255,
            green: Double(g) / 255,
            blue: Double(b) / 255,
            opacity: Double(a) / 255
        )
    }
}

enum PatchaTheme {
    static let accent = Color(hex: "CDF064")
    static let lightAccent = Color(hex: "A0BD4B")
    static let sidebarTint = Color.black.opacity(0.18)
    static let contentTint = Color.black.opacity(0.08)
    static let softDivider = Color.primary.opacity(0.08)
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
                        .background(PatchaTheme.sidebarTint.ignoresSafeArea())
                        .ignoresSafeArea(edges: .top)
                    contentView
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(PatchaTheme.contentTint.ignoresSafeArea())
                        .ignoresSafeArea(edges: .top)
                }
                .frame(width: 700)
                .frame(maxHeight: .infinity)
                .onAppear { permissionsVM.scan() }
            }
        }
        .tint(colorScheme == .dark ? PatchaTheme.accent : PatchaTheme.lightAccent)
        .accentColor(colorScheme == .dark ? PatchaTheme.accent : PatchaTheme.lightAccent)
    }
}
