import AppKit
import SwiftUI

private enum PermissionsTab: CaseIterable {
    case apps, websites
    var title: String {
        switch self {
        case .apps:     return "Apps"
        case .websites: return "Web"
        }
    }
}

struct AppPermissionsPane: View {
    @ObservedObject var store: SettingsStore
    @ObservedObject var viewModel: AppPermissionsViewModel
    var embedded: Bool = false
    @State private var searchText = ""
    @State private var selectedTab: PermissionsTab = .apps
    @State private var websiteInput = ""
    @Environment(\.colorScheme) private var colorScheme
    @State private var isAddingWebsite = false

    var body: some View {
        if embedded {
            content
        } else {
            VStack(spacing: 0) {
                tabHeader
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)

                ScrollView {
                    tabContent
                        .padding(.horizontal, 24)
                        .padding(.vertical, 22)
                }
                .scrollIndicators(.hidden)
                .background(ScrollerHider())
            }
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 20) {
            tabHeader
            tabContent
        }
        .padding(.horizontal, embedded ? 0 : 24)
        .padding(.top, embedded ? 0 : 22)
    }

    @ViewBuilder
    private var tabContent: some View {
        switch selectedTab {
        case .apps:
            VStack(alignment: .leading, spacing: 28) {
                sourcesSection
                appsSection
            }
        case .websites:
            websitesSection
        }
    }

    private var tabHeader: some View {
        HStack(spacing: 4) {
            ForEach(PermissionsTab.allCases, id: \.self) { tab in
                Button {
                    withAnimation(.spring(duration: 0.2)) { selectedTab = tab }
                } label: {
                    Text(tab.title)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(selectedTab == tab ? .white : Color.secondary)
                        .frame(minWidth: 80)
                        .padding(.horizontal, 20)
                        .padding(.vertical, 7)
                        .background(
                            Capsule(style: .continuous)
                                .fill(selectedTab == tab ? Color.accentColor : Color.clear)
                        )
                        .contentShape(Capsule(style: .continuous))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(3)
        .background(Capsule(style: .continuous).fill(Color.primary.opacity(0.07)))
        .frame(maxWidth: .infinity, alignment: .center)
    }

    private var sourcesSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionLabel("Data Sources")

            VStack(spacing: 0) {
                SourceTile(
                    iconName: "terminal.fill",
                    svgName: "terminal",
                    name: "Terminal",
                    description: "Shell history, commands, output",
                    isOn: persistingBinding(get: { store.enableTerminal }, set: { store.enableTerminal = $0 })
                )


                SourceTile(
                    iconName: "arrow.triangle.branch",
                    svgName: "git",
                    name: "Git",
                    description: "Commits, branches, staging area",
                    isOn: persistingBinding(get: { store.enableGit }, set: { store.enableGit = $0 })
                )
            }
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(PatchaTheme.bg(for: colorScheme))
            )
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Color.primary.opacity(0.08), lineWidth: 0.5))
        }
    }

    private var appsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                sectionLabel("Excluded Apps")
                Spacer()
                Button("Rescan") { viewModel.scan() }
                    .buttonStyle(.plain)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Color.accentColor)
            }

            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 11))
                    .foregroundStyle(Color.secondary)
                TextField("Filter apps…", text: $searchText)
                    .textFieldStyle(.plain)
                    .font(.system(size: 12))
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(PatchaTheme.bg(for: colorScheme))
            )

            appListContainer
        }
    }

    @ViewBuilder
    private var appListContainer: some View {
        if viewModel.isScanning {
            HStack {
                Spacer()
                VStack(spacing: 8) {
                    ProgressView()
                    Text("Scanning…")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.secondary)
                }
                .padding(.vertical, 28)
                Spacer()
            }
        } else {
            let items = filteredApps
            if items.isEmpty {
                Text(searchText.isEmpty ? "No apps detected." : "No matches for \"\(searchText)\".")
                    .font(.system(size: 11))
                    .foregroundStyle(Color.secondary)
                    .padding(.vertical, 20)
                    .frame(maxWidth: .infinity)
                    .background(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(PatchaTheme.bg(for: colorScheme))
                    )
            } else {
                VStack(spacing: 0) {
                    ForEach(items) { app in
                        AppPermissionRow(app: app) { viewModel.toggle(app) }
                    }
                }
                .background(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(PatchaTheme.bg(for: colorScheme))
                )
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Color.primary.opacity(0.08), lineWidth: 0.5))
            }
        }
    }

    private var websitesSection: some View {
        VStack(alignment: .leading, spacing: 22) {
            VStack(alignment: .leading, spacing: 10) {
                sectionLabel("Browser Tracking")
                BrowserHistoryTile(
                    isOn: persistingBinding(get: { store.enableBrowser }, set: { store.enableBrowser = $0 })
                )
            }

            VStack(alignment: .leading, spacing: 10) {
                sectionLabel("Excluded Websites")

                websiteInputRow

                if store.websiteExclusions.isEmpty {
                    Text("No exclusions — all sites are tracked.")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.secondary)
                        .padding(.vertical, 18)
                        .frame(maxWidth: .infinity)
                        .background(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .fill(PatchaTheme.bg(for: colorScheme))
                        )
                } else {
                    VStack(spacing: 0) {
                        ForEach(store.websiteExclusions) { entry in
                            WebsiteRow(entry: entry) {
                                viewModel.toggleWebsite(entry)
                            } onDelete: {
                                viewModel.removeWebsite(entry.domain)
                            }
                        }
                    }
                    .background(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(PatchaTheme.bg(for: colorScheme))
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Color.primary.opacity(0.08), lineWidth: 0.5))
                }
            }
        }
    }

    private var websiteInputRow: some View {
        HStack(spacing: 8) {
            Image(systemName: "globe")
                .font(.system(size: 11))
                .foregroundStyle(Color.secondary)

            TextField("Add domain (e.g. twitter.com)", text: $websiteInput)
                .textFieldStyle(.plain)
                .font(.system(size: 12))
                .onSubmit { submitWebsite() }

            if isAddingWebsite {
                ProgressView().controlSize(.small)
            } else if !websiteInput.isEmpty {
                Button { submitWebsite() } label: {
                    Image(systemName: "return")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Color.accentColor)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(PatchaTheme.bg(for: colorScheme))
        )
        .animation(.easeOut(duration: 0.15), value: websiteInput.isEmpty)
    }

    private func submitWebsite() {
        let input = websiteInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty else { return }
        websiteInput = ""
        isAddingWebsite = true
        Task {
            await viewModel.addWebsite(input)
            isAddingWebsite = false
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

    @ViewBuilder
    private func sectionLabel(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.system(size: 9, weight: .bold))
            .tracking(2.5)
            .foregroundStyle(Color.secondary)
    }
}

private struct SourceTile: View {
    let iconName: String
    var svgName: String? = nil
    let name: String
    let description: String
    @Binding var isOn: Bool
    @Environment(\.colorScheme) private var colorScheme

    private var svgImage: NSImage? {
        guard let n = svgName,
              let path = Bundle.main.path(forResource: n, ofType: "svg") else { return nil }
        return NSImage(contentsOfFile: path)
    }

    var body: some View {
        HStack(spacing: 0) {
            HStack(spacing: 12) {
                Group {
                    if let img = svgImage {
                        Image(nsImage: img)
                            .renderingMode(.template)
                            .resizable()
                            .scaledToFit()
                            .frame(width: 15, height: 15)
                    } else {
                        Image(systemName: iconName)
                            .font(.system(size: 13))
                    }
                }
                .foregroundStyle(isOn ? Color.accentColor : Color.secondary)
                .frame(width: 20)
                .animation(.easeOut(duration: 0.2), value: isOn)

                VStack(alignment: .leading, spacing: 3) {
                    Text(name)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Color.primary)

                    Text(description)
                        .font(.system(size: 11))
                        .foregroundStyle(Color.secondary)
                }

                Spacer(minLength: 8)

                Toggle("", isOn: $isOn)
                    .labelsHidden()
                    .toggleStyle(.switch)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 14)
            .background(
                Group {
                    if isOn {
                        LinearGradient(
                            colors: [Color.accentColor.opacity(0.06), Color.clear],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    } else {
                        Color.clear
                    }
                }
                .animation(.easeOut(duration: 0.25), value: isOn)
            )
        }
    }
}

private struct BrowserHistoryTile: View {
    @Binding var isOn: Bool
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(spacing: 0) {
            HStack(spacing: 12) {
                Group {
                    if let path = Bundle.main.path(forResource: "web", ofType: "svg"),
                       let img = NSImage(contentsOfFile: path) {
                        Image(nsImage: img)
                            .renderingMode(.template)
                            .resizable()
                            .scaledToFit()
                            .frame(width: 15, height: 15)
                    } else {
                        Image(systemName: "globe")
                            .font(.system(size: 13))
                    }
                }
                .foregroundStyle(isOn ? Color.accentColor : Color.secondary)
                .frame(width: 20)
                .animation(.easeOut(duration: 0.2), value: isOn)

                VStack(alignment: .leading, spacing: 3) {
                    Text("Browser History")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Color.primary)

                    Text("URLs and page titles you visit")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.secondary)
                }

                Spacer(minLength: 8)

                Toggle("", isOn: $isOn)
                    .labelsHidden()
                    .toggleStyle(.switch)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 14)
            .background(
                Group {
                    if isOn {
                        LinearGradient(
                            colors: [Color.accentColor.opacity(0.06), Color.clear],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    } else {
                        Color.clear
                    }
                }
                .animation(.easeOut(duration: 0.25), value: isOn)
            )
        }
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(PatchaTheme.bg(for: colorScheme))
        )
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Color.primary.opacity(0.08), lineWidth: 0.5))
    }
}

private struct WebsiteRow: View {
    let entry: WebsiteEntry
    let onToggle: () -> Void
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Group {
                if let data = entry.faviconData, let image = NSImage(data: data) {
                    Image(nsImage: image)
                        .resizable()
                        .scaledToFit()
                        .frame(width: 14, height: 14)
                } else {
                    Image(systemName: "globe")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.secondary)
                }
            }
            .frame(width: 26, height: 26)
            .background(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(Color.primary.opacity(0.06))
            )

            Text(entry.domain)
                .font(.system(size: 12))
                .foregroundStyle(Color.primary)
                .lineLimit(1)

            Spacer()

            Button(action: onDelete) {
                Image(systemName: "xmark")
                    .font(.system(size: 8, weight: .semibold))
                    .foregroundStyle(Color.secondary)
                    .frame(width: 20, height: 20)
                    .background(Circle().fill(Color.primary.opacity(0.06)))
            }
            .buttonStyle(.plain)

            Toggle("", isOn: Binding(
                get: { entry.isExcluded },
                set: { _ in onToggle() }
            ))
            .labelsHidden()
            .toggleStyle(.switch)
        }
        .padding(.horizontal, 14)
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
                    .frame(width: 24, height: 24)
                    .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).stroke(Color.primary.opacity(0.08), lineWidth: 0.5))
            } else {
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(Color.primary.opacity(0.06))
                    .frame(width: 24, height: 24)
                    .overlay(
                        Image(systemName: "app")
                            .font(.system(size: 10))
                            .foregroundStyle(Color.secondary)
                    )
            }

            Text(app.name)
                .font(.system(size: 12))
                .foregroundStyle(Color.primary)
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
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
    }
}

struct SoftDivider: View {
    var body: some View {
        Rectangle()
            .fill(Color(.separatorColor))
            .frame(height: 1)
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
