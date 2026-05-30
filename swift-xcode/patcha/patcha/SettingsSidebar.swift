import AppKit
import SwiftUI

struct SettingsSidebar: View {
    @Binding var selected: SettingsSection

    private var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Spacer()
                .frame(height: 52)

            ForEach(SettingsSection.allCases, id: \.self) { s in
                SidebarNavItem(section: s, isSelected: selected == s) {
                    selected = s
                }
            }

            Spacer()

            VStack(alignment: .leading, spacing: 4) {
                if let icon = NSApp.applicationIconImage {
                    Image(nsImage: icon)
                        .resizable()
                        .frame(width: 40, height: 40)
                }
                Text("patcha \(Text("v\(appVersion)").foregroundColor(.secondary))")
                    .font(.britanica(13))
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 20)
        }
    }
}

struct SidebarNavItem: View {
    let section: SettingsSection
    let isSelected: Bool
    let action: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let accent = PatchaTheme.accent
        let bgColor = PatchaTheme.bg(for: colorScheme)
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: section.systemImage)
                    .frame(width: 16)
                Text(section.title)
                    .font(.britanica(13))
                Spacer()
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(isSelected ? accent : Color.clear)
            )
            .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
        .buttonStyle(.plain)
        .foregroundStyle(isSelected ? bgColor : Color.primary)
        .padding(.horizontal, 8)
        .padding(.vertical, 2)
    }
}
