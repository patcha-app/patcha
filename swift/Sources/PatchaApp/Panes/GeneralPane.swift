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
            .padding(.bottom, 16)

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
            .scrollContentBackground(.hidden)

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
                }
                .buttonStyle(.borderedProminent)
                .task(id: restartFeedback) {
                    guard restartFeedback else { return }
                    try? await Task.sleep(for: .seconds(3))
                    restartFeedback = false
                }
            }
            .padding()
        }
    }
}
