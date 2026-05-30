import SwiftUI

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

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                behaviorSection
                pauseSection
                Spacer(minLength: 0)
                footer
            }
            .padding(20)
        }
        .scrollIndicators(.hidden)
        .background(ScrollerHider())
    }

    private var behaviorSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Behavior")

            VStack(spacing: 0) {
                SettingsToggleRow(label: "Pause collection when Patcha is focused", isOn: $store.pauseForInternal)
                SoftDivider().padding(.horizontal, 16)
                SettingsToggleRow(label: "Launch at Login", isOn: $store.launchAtLogin)
                SoftDivider().padding(.horizontal, 16)
                SettingsToggleRow(label: "Check for updates automatically", isOn: $store.autoCheckUpdates)
            }
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color.primary.opacity(0.05))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(PatchaTheme.softDivider, lineWidth: 1)
            )
        }
    }

    private var pauseSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title:"Pause Patcha")

            VStack(spacing: 0) {
                if isPaused, let until = daemonManager.pausedUntil {
                    SettingsRow(label: "Status") {
                        Text("Paused · resumes \(until.formatted(.relative(presentation: .named)))")
                            .font(.britanica(13))
                            .foregroundStyle(.secondary)
                    }
                    SoftDivider().padding(.horizontal, 16)
                    SettingsRow(label: "") {
                        Button("Resume Now") { daemonManager.resume() }
                            .buttonStyle(AccentButtonStyle())
                    }
                } else {
                    SettingsRow(label: "Pause for") {
                        Picker("", selection: $pauseDuration) {
                            ForEach(pauseOptions, id: \.1) { label, secs in
                                Text(label).tag(secs)
                            }
                        }
                        .labelsHidden()
                        .font(.britanica(13))
                        .frame(width: 160)
                    }
                    SoftDivider().padding(.horizontal, 16)
                    SettingsRow(label: "") {
                        Button("Pause Recording") {
                            let until = pauseDuration == -1
                                ? nextMidnight()
                                : Date().addingTimeInterval(TimeInterval(pauseDuration))
                            daemonManager.pause(until: until)
                        }
                        .buttonStyle(AccentButtonStyle())
                    }
                }
            }
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color.primary.opacity(0.05))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(PatchaTheme.softDivider, lineWidth: 1)
            )
        }
    }

    private var footer: some View {
        HStack {
            if restartFeedback {
                Text("Saved. Restarting daemon...")
                    .font(.britanica(13))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Save & Restart Daemon") {
                store.save { daemonManager.restart() }
                restartFeedback = true
            }
            .buttonStyle(AccentButtonStyle())
            .task(id: restartFeedback) {
                guard restartFeedback else { return }
                try? await Task.sleep(for: .seconds(3))
                restartFeedback = false
            }
        }
    }
}

private struct SettingsToggleRow: View {
    let label: String
    @Binding var isOn: Bool

    var body: some View {
        HStack {
            Text(label)
                .font(.britanica(13))
            Spacer()
            Toggle("", isOn: $isOn)
                .labelsHidden()
                .toggleStyle(.switch)
                .tint(PatchaTheme.accent)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 11)
    }
}

private struct SettingsRow<Content: View>: View {
    let label: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        HStack {
            if !label.isEmpty {
                Text(label)
                    .font(.britanica(13))
            }
            Spacer()
            content()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 11)
    }
}

