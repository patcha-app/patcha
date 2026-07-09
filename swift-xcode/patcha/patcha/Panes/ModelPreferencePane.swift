import SwiftUI

struct ModelPreferencePane: View {
    @ObservedObject var store: SettingsStore
    @Environment(\.colorScheme) private var colorScheme

    private let port: Int
    @State private var availability: [String: Bool] = ["api": true]

    init(mcpManager: MCPManager, store: SettingsStore) {
        self.store = store
        self.port = mcpManager.mcpPort()
    }

    var body: some View {
        VStack(spacing: 0) {
            header
                .padding(.horizontal, 28)
                .padding(.top, 24)
                .padding(.bottom, 18)

            SoftDivider()

            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(ChatBackend.allCases) { backend in
                        BackendRow(
                            backend: backend,
                            isSelected: store.chatBackend == backend.rawValue,
                            isAvailable: availability[backend.rawValue] ?? !backend.requiresCLI,
                            onTap: { select(backend) }
                        )
                    }

                    Text("Local agents call the same patcha activity tools through the built-in MCP server. They need the CLI installed and signed in, and they run on your own Claude or OpenAI account.")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .padding(.top, 6)
                        .padding(.horizontal, 2)
                }
                .padding(.horizontal, 28)
                .padding(.top, 22)
                .padding(.bottom, 40)
            }
            .scrollIndicators(.hidden)
            .background(ScrollerHider())
        }
        .task { await loadAvailability() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Model Preference")
                .font(.system(size: 22, weight: .bold))
                .foregroundStyle(PatchaTheme.primaryText(for: colorScheme))
            Text("Choose what powers Chat")
                .font(.system(size: 12))
                .foregroundStyle(PatchaTheme.secondaryText(for: colorScheme))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func select(_ backend: ChatBackend) {
        let available = availability[backend.rawValue] ?? !backend.requiresCLI
        guard available else { return }
        store.setChatBackend(backend.rawValue)
    }

    private func loadAvailability() async {
        guard port > 0,
              let url = URL(string: "http://127.0.0.1:\(port)/api/chat/backends") else { return }
        var request = URLRequest(url: url, timeoutInterval: 6)
        request.httpMethod = "GET"
        guard let (data, _) = try? await URLSession.shared.data(for: request),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Bool]
        else { return }
        availability = obj

        // If the saved backend is no longer available, fall back to built-in.
        if let chosen = ChatBackend(rawValue: store.chatBackend),
           chosen.requiresCLI, obj[chosen.rawValue] == false {
            store.setChatBackend(ChatBackend.api.rawValue)
        }
    }
}

private struct BackendRow: View {
    let backend: ChatBackend
    let isSelected: Bool
    let isAvailable: Bool
    let onTap: () -> Void

    @Environment(\.colorScheme) private var colorScheme
    @State private var hover = false

    var body: some View {
        Button(action: onTap) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: backend.systemImage)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(isSelected ? PatchaTheme.accent : .secondary)
                    .frame(width: 22)
                    .padding(.top, 1)

                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        Text(backend.title)
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(PatchaTheme.primaryText(for: colorScheme))
                        if backend.requiresCLI && !isAvailable {
                            Text("Not installed")
                                .font(.system(size: 10, weight: .semibold))
                                .foregroundStyle(.secondary)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Capsule().fill(Color.primary.opacity(0.08)))
                        }
                    }
                    Text(backend.subtitle)
                        .font(.system(size: 12))
                        .foregroundStyle(PatchaTheme.secondaryText(for: colorScheme))
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 8)

                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 16))
                    .foregroundStyle(isSelected ? PatchaTheme.accent : Color.secondary.opacity(0.4))
                    .padding(.top, 1)
            }
            .padding(14)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(isSelected ? PatchaTheme.selectedOverlay(for: colorScheme)
                                     : PatchaTheme.tile(for: colorScheme).opacity(hover ? 1 : 0.7))
            )
            .contentShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .buttonStyle(.plain)
        .opacity(isAvailable ? 1 : 0.55)
        .disabled(!isAvailable)
        .onHover { hover = $0 }
    }
}
