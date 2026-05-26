import AppKit
import SwiftUI

struct AppPermissionsPane: View {
    @ObservedObject var store: SettingsStore
    @ObservedObject var viewModel: AppPermissionsViewModel
    var embedded: Bool = false
    @State private var searchText: String = ""

    var body: some View {
        if embedded {
            content
        } else {
            ScrollView { content.padding(.bottom, 20) }
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 24) {
            sourcesSection
            appsSection
        }
        .padding(.horizontal, embedded ? 0 : 20)
        .padding(.top, embedded ? 0 : 20)
    }

    private var sourcesSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Background sources")
                .font(.system(size: 14, weight: .semibold))
            Text("Turn off any source and Patcha stops indexing it. Your screen is still seen — these are just specific data streams.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(alignment: .top, spacing: 12) {
                SourceTile(
                    iconName: "terminal",
                    name: "Terminal",
                    description: "Shell history, commands, output.",
                    isOn: persistingBinding(get: { store.enableTerminal }, set: { store.enableTerminal = $0 })
                )
                SourceTile(
                    iconName: "arrow.triangle.branch",
                    name: "Git",
                    description: "Commits, branches, staging.",
                    isOn: persistingBinding(get: { store.enableGit }, set: { store.enableGit = $0 })
                )
                SourceTile(
                    iconName: "globe",
                    name: "Browser history",
                    description: "URLs and titles you visit.",
                    isOn: persistingBinding(get: { store.enableBrowser }, set: { store.enableBrowser = $0 })
                )
            }
            .padding(.top, 4)
        }
    }

    private var appsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Exclude specific apps")
                        .font(.system(size: 14, weight: .semibold))
                    Text("When any of these is in focus, Patcha looks away completely — no screen, no text, nothing.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button("Rescan") { viewModel.scan() }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }

            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary)
                    .font(.system(size: 12))
                TextField("Search apps", text: $searchText)
                    .textFieldStyle(.plain)
                    .font(.system(size: 13))
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(
                Capsule(style: .continuous)
                    .fill(Color.primary.opacity(0.05))
            )
            .overlay(
                Capsule(style: .continuous)
                    .stroke(PatchaTheme.softDivider, lineWidth: 1)
            )

            appListContainer
        }
    }

    @ViewBuilder
    private var appListContainer: some View {
        if viewModel.isScanning {
            HStack {
                Spacer()
                ProgressView("Scanning apps...")
                    .padding(.vertical, 24)
                Spacer()
            }
        } else {
            let items = filteredApps
            if items.isEmpty {
                Text(searchText.isEmpty ? "No apps detected." : "No apps match “\(searchText)”.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 16)
                    .frame(maxWidth: .infinity)
                    .background(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(Color.primary.opacity(0.04))
                    )
            } else {
                LazyVStack(spacing: 0) {
                    ForEach(items) { app in
                        AppPermissionRow(app: app) { viewModel.toggle(app) }
                        if app.id != items.last?.id {
                            SoftDivider().padding(.leading, 56)
                        }
                    }
                }
                .background(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .fill(Color.primary.opacity(0.04))
                )
            }
        }
    }

    private var filteredApps: [AppEntry] {
        guard !searchText.isEmpty else { return viewModel.apps }
        return viewModel.apps.filter { $0.name.localizedCaseInsensitiveContains(searchText) }
    }

    private func persistingBinding(get: @escaping () -> Bool, set: @escaping (Bool) -> Void) -> Binding<Bool> {
        Binding(
            get: get,
            set: { newValue in
                set(newValue)
                store.save {}
            }
        )
    }
}

private struct SourceTile: View {
    let iconName: String
    let name: String
    let description: String
    @Binding var isOn: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                ZStack {
                    RoundedRectangle(cornerRadius: 9, style: .continuous)
                        .fill(PatchaTheme.accent.opacity(isOn ? 0.9 : 0.25))
                        .frame(width: 36, height: 36)
                    Image(systemName: iconName)
                        .font(.system(size: 15, weight: .medium))
                        .foregroundColor(isOn ? .white : .primary.opacity(0.6))
                }
                Spacer()
                Toggle("", isOn: $isOn)
                    .labelsHidden()
                    .toggleStyle(.switch)
                    .tint(PatchaTheme.accent)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(name)
                    .font(.system(size: 14, weight: .semibold))
                Text(description)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(PatchaTheme.accent.opacity(isOn ? 0.08 : 0.03))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(PatchaTheme.accent.opacity(isOn ? 0.35 : 0.15), lineWidth: 1)
        )
    }
}

struct SoftDivider: View {
    var body: some View {
        Rectangle()
            .fill(PatchaTheme.softDivider)
            .frame(height: 1)
    }
}

struct AppPermissionRow: View {
    let app: AppEntry
    let onToggle: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            if let icon = app.icon {
                Image(nsImage: icon)
                    .resizable()
                    .frame(width: 28, height: 28)
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            } else {
                Image(systemName: "app")
                    .font(.body)
                    .frame(width: 28, height: 28)
                    .background(
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Color.secondary.opacity(0.15))
                    )
            }
            Text(app.name)
                .font(.body)
                .lineLimit(1)
            Spacer()
            Toggle("", isOn: Binding(
                get: { !app.isExcluded },
                set: { _ in onToggle() }
            ))
            .labelsHidden()
            .toggleStyle(.switch)
            .accessibilityLabel(Text(app.name))
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}
