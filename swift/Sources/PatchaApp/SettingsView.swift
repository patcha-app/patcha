import AppKit
import SwiftUI

enum SettingsSection: CaseIterable, Hashable {
    case permissions, general, memories, modelPreference

    var title: String {
        switch self {
        case .permissions:     return "App Permissions"
        case .general:         return "General"
        case .memories:        return "Memories"
        case .modelPreference: return "Model Preference"
        }
    }

    var systemImage: String {
        switch self {
        case .permissions:     return "shield"
        case .general:         return "gearshape"
        case .memories:        return "clock"
        case .modelPreference: return "cpu"
        }
    }
}

struct SettingsRootView: View {
    @ObservedObject var store: SettingsStore
    var daemonManager: DaemonManager?
    @State private var section: SettingsSection = .permissions
    @StateObject private var permissionsVM: AppPermissionsViewModel

    init(store: SettingsStore, daemonManager: DaemonManager?) {
        self.store = store
        self.daemonManager = daemonManager
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
        }
    }

    var body: some View {
        HStack(spacing: 0) {
            SettingsSidebar(selected: $section)
                .frame(width: 220)
                .ignoresSafeArea(edges: .top)
            Rectangle()
                .fill(Color(NSColor.separatorColor))
                .frame(width: 0.5)
                .ignoresSafeArea(edges: .top)
            contentView
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .ignoresSafeArea(edges: .top)
        }
        .frame(width: 700)
        .frame(maxHeight: .infinity)
        .onAppear { permissionsVM.scan() }
    }
}

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
            .padding(.bottom, 12)

            Divider()
                .padding(.bottom, 4)

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

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: section.systemImage)
                    .frame(width: 16)
                Text(section.title)
                    .font(.callout)
                Spacer()
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(isSelected ? Color.accentColor.opacity(0.12) : Color.clear)
            .cornerRadius(6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(isSelected ? Color.accentColor : Color.primary)
        .padding(.horizontal, 8)
        .padding(.vertical, 2)
    }
}

struct AppPermissionsPane: View {
    @ObservedObject var store: SettingsStore
    @ObservedObject var viewModel: AppPermissionsViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 8) {
                        Image(systemName: "shield")
                            .font(.title2)
                        Text("App Permissions")
                            .font(.title2)
                            .fontWeight(.semibold)
                    }
                    Text("Control which applications Patcha is allowed to observe and extract context from.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Rescan Apps") {
                    viewModel.scan()
                }
                .buttonStyle(.bordered)
            }
            .padding(.horizontal, 20)
            .padding(.top, 32)
            .padding(.bottom, 12)

            Divider()

            if viewModel.isScanning {
                Spacer()
                ProgressView("Scanning apps...")
                    .frame(maxWidth: .infinity)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        CollectorRow(iconName: "arrow.triangle.branch", name: "Git", isEnabled: $store.enableGit)
                        Divider().padding(.leading, 56)
                        CollectorRow(iconName: "safari", name: "Browser", isEnabled: $store.enableBrowser)
                        Divider()
                        ForEach(viewModel.apps) { app in
                            AppPermissionRow(app: app) { viewModel.toggle(app) }
                            if app.id != viewModel.apps.last?.id {
                                Divider().padding(.leading, 56)
                            }
                        }
                    }
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(.separator, lineWidth: 1))
                    .padding(16)
                }
            }
        }
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
                .background(Color.secondary.opacity(0.15), in: RoundedRectangle(cornerRadius: 6))
            Text(name)
                .font(.body)
            Spacer()
            Toggle("", isOn: $isEnabled)
                .labelsHidden()
                .toggleStyle(.switch)
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
                    .cornerRadius(6)
            } else {
                Image(systemName: "app")
                    .font(.body)
                    .frame(width: 28, height: 28)
                    .background(Color.secondary.opacity(0.15), in: RoundedRectangle(cornerRadius: 6))
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
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}

struct GeneralPane: View {
    @ObservedObject var store: SettingsStore
    @ObservedObject var daemonManager: DaemonManager
    @State private var restartFeedback = false
    @State private var pauseDuration: Int = 30 * 60

    private let pauseOptions: [(String, Int)] = [
        ("30 minutes", 30 * 60),
        ("1 hour",     60 * 60),
        ("2 hours",   120 * 60),
        ("4 hours",   240 * 60),
        ("Until tomorrow", -1),
    ]

    private var isPaused: Bool {
        daemonManager.status == .paused
    }

    private func nextMidnight() -> Date {
        var comps = Calendar.current.dateComponents([.year, .month, .day], from: Date())
        comps.day = (comps.day ?? 0) + 1
        comps.hour = 0; comps.minute = 0; comps.second = 0
        return Calendar.current.date(from: comps) ?? Date().addingTimeInterval(86400)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "gearshape")
                    .font(.title2)
                Text("General")
                    .font(.title2)
                    .fontWeight(.semibold)
            }
            .padding(.horizontal, 20)
            .padding(.top, 32)
            .padding(.bottom, 12)

            Divider()

            Form {
                Section("Behavior") {
                    Toggle("Pause collection when Patcha is focused", isOn: $store.pauseForInternal)
                    Toggle("Launch at Login", isOn: $store.launchAtLogin)
                    Toggle("Check for updates automatically", isOn: $store.autoCheckUpdates)
                }
                Section("Pause Patcha") {
                    if isPaused, let until = daemonManager.pausedUntil {
                        LabeledContent("Status") {
                            Text("Paused · resumes \(until.formatted(.relative(presentation: .named)))")
                                .foregroundStyle(.secondary)
                        }
                        Button("Resume Now") {
                            daemonManager.resume()
                        }
                    } else {
                        LabeledContent("Pause for") {
                            Picker("", selection: $pauseDuration) {
                                ForEach(pauseOptions, id: \.1) { label, secs in
                                    Text(label).tag(secs)
                                }
                            }
                            .labelsHidden()
                            .frame(width: 160)
                        }
                        Button("Pause Recording") {
                            let until = pauseDuration == -1
                                ? nextMidnight()
                                : Date().addingTimeInterval(TimeInterval(pauseDuration))
                            daemonManager.pause(until: until)
                        }
                    }
                }
            }
            .formStyle(.grouped)

            Divider()

            HStack {
                if restartFeedback {
                    Text("Saved. Restarting daemon...")
                        .foregroundStyle(.secondary)
                        .font(.callout)
                }
                Spacer()
                Button("Save & Restart Daemon") {
                    store.save { daemonManager.restart() }
                    restartFeedback = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                        restartFeedback = false
                    }
                }
                .buttonStyle(.borderedProminent)
            }
            .padding()
        }
    }
}

struct PlaceholderPane: View {
    let section: SettingsSection

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: section.systemImage)
                .font(.system(size: 40))
                .foregroundStyle(.tertiary)
            Text(section.title)
                .font(.title2)
                .fontWeight(.semibold)
            Text("Coming soon")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
