import AppKit
import SwiftUI

struct AppPermissionsPane: View {
    @ObservedObject var store: SettingsStore
    @ObservedObject var viewModel: AppPermissionsViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Control which applications Patcha is allowed to observe and extract context from.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Rescan Apps") {
                    viewModel.scan()
                }
                .buttonStyle(.bordered)
            }
            .padding(.horizontal, 20)
            .padding(.top, 20)
            .padding(.bottom, 16)

            if viewModel.isScanning {
                Spacer()
                ProgressView("Scanning apps...")
                    .frame(maxWidth: .infinity)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        CollectorRow(iconName: "arrow.triangle.branch", name: "Git", isEnabled: $store.enableGit)
                        SoftDivider().padding(.leading, 56)
                        CollectorRow(iconName: "safari", name: "Browser", isEnabled: $store.enableBrowser)
                        SoftDivider()
                        ForEach(viewModel.apps) { app in
                            AppPermissionRow(app: app) { viewModel.toggle(app) }
                            if app.id != viewModel.apps.last?.id {
                                SoftDivider().padding(.leading, 56)
                            }
                        }
                    }
                    .background(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(Color.primary.opacity(0.04))
                    )
                    .padding(16)
                }
            }
        }
    }
}

struct SoftDivider: View {
    var body: some View {
        Rectangle()
            .fill(PatchaTheme.softDivider)
            .frame(height: 1)
    }
}

struct CollectorRow: View {
    let iconName: String
    let name: String
    @Binding var isEnabled: Bool

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: iconName)
                .font(.body)
                .frame(width: 28, height: 28)
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(Color.secondary.opacity(0.15))
                )
            Text(name)
                .font(.body)
            Spacer()
            Toggle("", isOn: $isEnabled)
                .labelsHidden()
                .toggleStyle(.switch)
                .accessibilityLabel(Text(name))
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
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
