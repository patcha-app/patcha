import SwiftUI

struct SettingsView: View {
    @ObservedObject var store: SettingsStore
    var onSave: () -> Void

    @State private var saved = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Form {
                Section("Collection") {
                    LabeledContent("Poll Interval") {
                        Stepper("\(store.pollInterval) s", value: $store.pollInterval, in: 30...600, step: 30)
                    }
                }

                Section("Collectors") {
                    Toggle("Git", isOn: $store.enableGit)
                    Toggle("Browser", isOn: $store.enableBrowser)
                    Toggle("Terminal", isOn: $store.enableTerminal)
                    Toggle("Window", isOn: $store.enableWindow)
                    Toggle("Accessibility", isOn: $store.enableAccessibility)
                }
            }
            .formStyle(.grouped)

            Divider()

            HStack {
                if saved {
                    Text("Saved. Restarting daemon...")
                        .foregroundStyle(.secondary)
                        .font(.callout)
                }
                Spacer()
                Button("Save & Restart Daemon") {
                    store.save {
                        onSave()
                    }
                    saved = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                        saved = false
                    }
                }
                .buttonStyle(.borderedProminent)
            }
            .padding()
        }
        .frame(width: 400)
    }
}
