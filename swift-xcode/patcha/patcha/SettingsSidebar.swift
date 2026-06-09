import AppKit
import SwiftUI
import Supabase

struct SettingsSidebar: View {
    @Binding var selected: SettingsSection
    @ObservedObject var authManager: AuthManager

    private let topSections: [SettingsSection] = [.permissions]
    private let activitySections: [SettingsSection] = [.timeline, .memories]
    private let settingsSections: [SettingsSection] = [.general, .modelPreference, .integrations]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Spacer()
                .frame(height: 52)

            ForEach(topSections, id: \.self) { s in
                SidebarNavItem(section: s, isSelected: selected == s) { selected = s }
            }

            SidebarSectionHeader(title: "Activity")
            ForEach(activitySections, id: \.self) { s in
                SidebarNavItem(section: s, isSelected: selected == s) { selected = s }
            }

            SidebarSectionHeader(title: "Settings")
            ForEach(settingsSections, id: \.self) { s in
                SidebarNavItem(section: s, isSelected: selected == s) { selected = s }
            }

            Spacer()

            SidebarAccountFooter(authManager: authManager)
                .padding(.horizontal, 8)
                .padding(.bottom, 12)
        }
    }
}

struct SidebarSectionHeader: View {
    let title: String

    var body: some View {
        Text(title)
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 4)
    }
}

struct SidebarAccountFooter: View {
    @ObservedObject var authManager: AuthManager
    @State private var expanded = false
    @State private var isSigningOut = false
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(spacing: 4) {
            if expanded {
                Button(isSigningOut ? "Signing out…" : "Sign Out") {
                    isSigningOut = true
                    Task {
                        defer { isSigningOut = false }
                        try? await authManager.signOut()
                        expanded = false
                    }
                }
                .buttonStyle(.plain)
                .font(.pBody)
                .foregroundStyle(.red)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(PatchaTheme.tile(for: colorScheme))
                )
                .disabled(isSigningOut)
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }

            Button {
                withAnimation(.spring(duration: 0.25)) { expanded.toggle() }
            } label: {
                HStack(spacing: 10) {
                    AsyncImage(url: authManager.avatarURL) { image in
                        image.resizable().scaledToFill()
                    } placeholder: {
                        Image(systemName: "person.circle.fill")
                            .font(.system(size: 28))
                            .foregroundStyle(.secondary)
                    }
                    .frame(width: 32, height: 32)
                    .clipShape(Circle())

                    VStack(alignment: .leading, spacing: 1) {
                        if let name = authManager.session?.user.userMetadata["full_name"]?.stringValue {
                            Text(name)
                                .font(.pBodyStrong)
                                .lineLimit(1)
                        }
                        Text(authManager.session?.user.email ?? "—")
                            .font(.pCaption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }

                    Spacer()

                    Image(systemName: expanded ? "chevron.down" : "chevron.up")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(PatchaTheme.tile(for: colorScheme))
                )
            }
            .buttonStyle(.plain)
        }
    }
}

struct SidebarNavItem: View {
    let section: SettingsSection
    let isSelected: Bool
    let action: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: section.systemImage)
                    .font(.system(size: 14, weight: .medium))
                    .frame(width: 18)
                Text(section.title)
                    .font(.pBody)
                Spacer()
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(isSelected ? PatchaTheme.selectedOverlay(for: colorScheme) : Color.clear)
            )
            .contentShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(.plain)
        .foregroundStyle(isSelected ? Color.primary : Color.secondary)
        .padding(.horizontal, 8)
        .padding(.vertical, 2)
    }
}
