import AppKit
import SwiftUI

struct SettingsSidebar: View {
    @Binding var selected: SettingsSection

    private var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                if let icon = NSApp.applicationIconImage {
                    Image(nsImage: icon)
                        .resizable()
                        .frame(width: 40, height: 40)
                }
                Text("Settings")
                    .font(.headline)
                    .fontWeight(.bold)
                Text("v\(appVersion)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 16)
            .padding(.top, 52)
            .padding(.bottom, 16)

            ForEach(SettingsSection.allCases, id: \.self) { s in
                SidebarNavItem(section: s, isSelected: selected == s) {
                    selected = s
                }
            }

            Spacer()
        }
    }
}

struct SidebarNavItem: View {
    let section: SettingsSection
    let isSelected: Bool
    let action: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let accent = colorScheme == .dark ? PatchaTheme.accent : PatchaTheme.lightAccent
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: section.systemImage)
                    .frame(width: 16)
                Text(section.title)
                    .font(.callout)
                Spacer()
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(isSelected ? accent.opacity(0.15) : Color.clear)
            )
            .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
        .buttonStyle(.plain)
        .foregroundStyle(isSelected ? accent : Color.primary)
        .padding(.horizontal, 8)
        .padding(.vertical, 2)
    }
}
