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
                .scrollIndicators(.hidden)
                .background(ScrollerHider())
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
            SectionHeader(title: "Background sources")
            Text("Turn off any source and Patcha stops indexing it. Your screen is still seen — these are just specific data streams.")
                .font(.pBody)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            LazyVStack(spacing: 0) {
                SourceTile(
                    iconName: "terminal.fill",
                    name: "Terminal",
                    description: "Shell history, commands, output.",
                    isOn: persistingBinding(get: { store.enableTerminal }, set: { store.enableTerminal = $0 })
                )
                SoftDivider().padding(.horizontal, 14)
                SourceTile(
                    iconName: "arrow.triangle.branch",
                    name: "Git",
                    description: "Commits, branches, staging.",
                    isOn: persistingBinding(get: { store.enableGit }, set: { store.enableGit = $0 })
                )
                SoftDivider().padding(.horizontal, 14)
                SourceTile(
                    iconName: "globe.fill",
                    name: "Browser history",
                    description: "URLs and titles you visit.",
                    isOn: persistingBinding(get: { store.enableBrowser }, set: { store.enableBrowser = $0 })
                )
            }
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color.primary.opacity(0.05))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(PatchaTheme.softDivider, lineWidth: 1)
            )
            .padding(.top, 4)
        }
    }

    private var appsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    SectionHeader(title: "Exclude specific apps")
                    Text("When any of these is in focus, Patcha looks away completely — no screen, no text, nothing.")
                        .font(.pBody)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button("Rescan") { viewModel.scan() }
                    .buttonStyle(.plain)
                    .font(.pBodyStrong)
                    .foregroundStyle(PatchaTheme.accent)
            }

            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary)
                    .font(.pIconSmall)
                TextField("Search apps", text: $searchText)
                    .textFieldStyle(.plain)
                    .font(.pBody)
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
                    .font(.pBody)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 16)
                    .frame(maxWidth: .infinity)
                    .background(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(Color.primary.opacity(0.05))
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
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .fill(Color.primary.opacity(0.05))
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
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 9, style: .continuous)
                            .fill(PatchaTheme.accent.opacity(isOn ? 0.9 : 0.25))
                            .frame(width: 32, height: 32)
                        Image(systemName: iconName)
                            .font(.pIconMedium)
                            .foregroundColor(isOn ? PatchaTheme.bg(for: colorScheme) : .primary.opacity(0.6))
                    }
                    Text(name)
                        .font(.pBodyStrong)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text(description)
                    .font(.pBody)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Toggle("", isOn: $isOn)
                .labelsHidden()
                .toggleStyle(.switch)
                .tint(PatchaTheme.accent)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .topLeading)
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
                Image(systemName: "app.fill")
                    .font(.pIconSmall)
                    .frame(width: 28, height: 28)
                    .background(
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Color.secondary.opacity(0.15))
                    )
            }
            Text(app.name)
                .font(.pBody)
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

struct ScrollerHider: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            var v: NSView? = view
            while let parent = v?.superview {
                if let scrollView = parent as? NSScrollView {
                    scrollView.hasVerticalScroller = false
                    scrollView.hasHorizontalScroller = false
                    break
                }
                v = parent
            }
        }
        return view
    }
    func updateNSView(_ view: NSView, context: Context) {}
}

#Preview {
    let store = SettingsStore()
    let viewModel = AppPermissionsViewModel(store: store)
    viewModel.apps = [
        AppEntry(id: "com.example.xcode", name: "Xcode", icon: nil, isExcluded: false),
        AppEntry(id: "com.example.slack", name: "Slack", icon: nil, isExcluded: true),
        AppEntry(id: "com.example.notes", name: "Notes", icon: nil, isExcluded: false),
        AppEntry(id: "com.example.safari", name: "Safari", icon: nil, isExcluded: false),
        AppEntry(id: "com.example.terminal", name: "Terminal", icon: nil, isExcluded: false),
    ]
    return AppPermissionsPane(store: store, viewModel: viewModel)
        .frame(width: 560, height: 700)
}
