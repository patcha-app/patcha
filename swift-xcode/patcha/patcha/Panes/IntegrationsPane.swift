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
        VStack(alignment: .leading, spacing: 0) {
            Form {
                Section("MCP Server") {
                    LabeledContent("Status") {
                        HStack(spacing: 6) {
                            Circle()
                                .fill(mcpManager.isRunning ? Color.green : Color.red)
                                .frame(width: 8, height: 8)
                            if mcpManager.isRunning {
                                let url = "http://127.0.0.1:\(String(mcpManager.mcpPort()))/mcp/"
                                Text(url)
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                                    .onTapGesture {
                                        NSPasteboard.general.clearContents()
                                        NSPasteboard.general.setString(url, forType: .string)
                                    }
                                    .help("Click to copy URL")
                            } else {
                                Text("Not running")
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                Section("Connect AI Clients") {
                    LabeledContent("Claude Code") {
                        HStack(spacing: 8) {
                            statusLabel(claudeCodeStatus)
                            Button("Connect") { connect(.claudeCode) }
                                .disabled(!mcpManager.isRunning)
                        }
                    }
                    .task(id: claudeCodeStatus) {
                        guard case .connected = claudeCodeStatus else { return }
                        try? await Task.sleep(for: .seconds(4))
                        claudeCodeStatus = .idle
                    }

                    LabeledContent("Claude Desktop") {
                        HStack(spacing: 8) {
                            statusLabel(claudeDesktopStatus)
                            Button("Connect") { connect(.claudeDesktop) }
                                .disabled(!mcpManager.isRunning)
                        }
                    }
                    .task(id: claudeDesktopStatus) {
                        guard case .connected = claudeDesktopStatus else { return }
                        try? await Task.sleep(for: .seconds(4))
                        claudeDesktopStatus = .idle
                    }

                    LabeledContent("ChatGPT / OpenAI") {
                        HStack(spacing: 8) {
                            if urlCopied {
                                Text("Copied!")
                                    .foregroundStyle(.secondary)
                                    .font(.callout)
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
                    .task(id: urlCopied) {
                        guard urlCopied else { return }
                        try? await Task.sleep(for: .seconds(3))
                        urlCopied = false
                    }
                }

                Section {
                    Text("ChatGPT: paste the URL in Settings \u{2192} Integrations \u{2192} Add MCP Server.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("OpenAI API: pass it as a remote_mcp tool in your Responses API call.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
            .scrollContentBackground(.hidden)
        }
    }

    @ViewBuilder
    private func statusLabel(_ status: ConnectStatus) -> some View {
        switch status {
        case .idle:
            EmptyView()
        case .connected:
            Text("Connected")
                .foregroundStyle(.secondary)
                .font(.callout)
        case .failed(let msg):
            Text(msg)
                .foregroundStyle(.red)
                .font(.callout)
        }
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
