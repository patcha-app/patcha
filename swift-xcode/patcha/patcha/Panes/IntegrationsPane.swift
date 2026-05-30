import AppKit
import SwiftUI

struct IntegrationsPane: View {
    @ObservedObject var mcpManager: MCPManager
    @State private var claudeCodeStatus: ConnectStatus = .idle
    @State private var claudeDesktopStatus: ConnectStatus = .idle
    @State private var urlCopied = false

    private enum ConnectStatus: Equatable {
        case idle, connected, failed(String)
    }

    private enum Client {
        case claudeCode, claudeDesktop
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                mcpServerSection
                connectClientsSection
                instructionsSection
            }
            .padding(20)
        }
        .scrollIndicators(.hidden)
        .background(ScrollerHider())
    }

    private var mcpServerSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "MCP Server")

            VStack(spacing: 0) {
                HStack {
                    Text("Status")
                        .font(.pBody)
                    Spacer()
                    HStack(spacing: 6) {
                        Circle()
                            .fill(mcpManager.isRunning ? Color.green : Color.red)
                            .frame(width: 8, height: 8)
                        if mcpManager.isRunning {
                            let url = "http://127.0.0.1:\(String(mcpManager.mcpPort()))/mcp/"
                            Text(url)
                                .font(.pBody)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                                .onTapGesture {
                                    NSPasteboard.general.clearContents()
                                    NSPasteboard.general.setString(url, forType: .string)
                                }
                                .help("Click to copy URL")
                        } else {
                            Text("Not running")
                                .font(.pBody)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 11)
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

    private var connectClientsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "Connect AI Clients")

            VStack(spacing: 0) {
                clientRow(label: "Claude Code", status: claudeCodeStatus) {
                    connect(.claudeCode)
                }
                .task(id: claudeCodeStatus) {
                    guard case .connected = claudeCodeStatus else { return }
                    try? await Task.sleep(for: .seconds(4))
                    claudeCodeStatus = .idle
                }

                SoftDivider().padding(.horizontal, 16)

                clientRow(label: "Claude Desktop", status: claudeDesktopStatus) {
                    connect(.claudeDesktop)
                }
                .task(id: claudeDesktopStatus) {
                    guard case .connected = claudeDesktopStatus else { return }
                    try? await Task.sleep(for: .seconds(4))
                    claudeDesktopStatus = .idle
                }

                SoftDivider().padding(.horizontal, 16)

                HStack {
                    Text("ChatGPT / OpenAI")
                        .font(.pBody)
                    Spacer()
                    HStack(spacing: 8) {
                        if urlCopied {
                            Text("Copied!")
                                .font(.pBody)
                                .foregroundStyle(.secondary)
                        }
                        Button("Copy MCP URL") {
                            let port = mcpManager.mcpPort()
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(
                                "http://127.0.0.1:\(port)/mcp/",
                                forType: .string
                            )
                            urlCopied = true
                        }
                        .disabled(!mcpManager.isRunning)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 11)
                .task(id: urlCopied) {
                    guard urlCopied else { return }
                    try? await Task.sleep(for: .seconds(3))
                    urlCopied = false
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

    private var instructionsSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("ChatGPT: paste the URL in Settings → Integrations → Add MCP Server.")
                .font(.pBody)
                .foregroundStyle(.secondary)
            Text("OpenAI API: pass it as a remote_mcp tool in your Responses API call.")
                .font(.pBody)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func clientRow(label: String, status: ConnectStatus, action: @escaping () -> Void) -> some View {
        HStack {
            Text(label)
                .font(.pBody)
            Spacer()
            HStack(spacing: 8) {
                switch status {
                case .idle:
                    EmptyView()
                case .connected:
                    Text("Connected")
                        .font(.pBody)
                        .foregroundStyle(.secondary)
                case .failed(let msg):
                    Text(msg)
                        .font(.pBody)
                        .foregroundStyle(.red)
                }
                Button("Connect", action: action)
                    .disabled(!mcpManager.isRunning)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 11)
    }

    private func connect(_ client: Client) {
        let port = mcpManager.mcpPort()
        let mcpURL = "http://127.0.0.1:\(port)/mcp/"
        let configPath: String
        switch client {
        case .claudeCode:
            configPath = NSHomeDirectory() + "/.claude.json"
        case .claudeDesktop:
            configPath = NSHomeDirectory() + "/Library/Application Support/Claude/claude_desktop_config.json"
        }

        Task {
            let result = writeConfig(path: configPath, mcpURL: mcpURL)
            await MainActor.run {
                switch client {
                case .claudeCode: claudeCodeStatus = result
                case .claudeDesktop: claudeDesktopStatus = result
                }
            }
        }
    }

    private func writeConfig(path: String, mcpURL: String) -> ConnectStatus {
        var config: [String: Any] = [:]
        if let data = FileManager.default.contents(atPath: path),
           let existing = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            config = existing
        }

        var mcpServers = config["mcpServers"] as? [String: Any] ?? [:]
        mcpServers["patcha"] = ["type": "http", "url": mcpURL]
        config["mcpServers"] = mcpServers

        let parent = (path as NSString).deletingLastPathComponent
        try? FileManager.default.createDirectory(
            atPath: parent,
            withIntermediateDirectories: true,
            attributes: nil
        )

        guard let data = try? JSONSerialization.data(
            withJSONObject: config,
            options: [.prettyPrinted]
        ) else {
            return .failed("JSON serialization failed")
        }

        do {
            try data.write(to: URL(fileURLWithPath: path))
            return .connected
        } catch {
            return .failed(error.localizedDescription)
        }
    }
}
