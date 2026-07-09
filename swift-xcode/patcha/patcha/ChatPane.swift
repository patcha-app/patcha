import SwiftUI

struct ChatPane: View {
    @StateObject private var viewModel: ChatViewModel
    @Environment(\.colorScheme) private var colorScheme
    @FocusState private var inputFocused: Bool

    init(mcpManager: MCPManager, store: SettingsStore) {
        _viewModel = StateObject(wrappedValue: ChatViewModel(port: mcpManager.mcpPort(), store: store))
    }

    var body: some View {
        VStack(spacing: 0) {
            if viewModel.isEmpty {
                emptyState
            } else {
                transcript
            }

            composer
                .padding(.horizontal, 28)
                .padding(.top, 12)

            privacyNote
                .padding(.horizontal, 28)
                .padding(.top, 7)
                .padding(.bottom, 16)
        }
        .overlay(alignment: .topTrailing) {
            newChatButton
                .padding(.top, 18)
                .padding(.trailing, 20)
        }
        .onAppear { inputFocused = true }
    }

    // MARK: - Privacy note

    private var privacyNote: some View {
        HStack(spacing: 5) {
            Image(systemName: "lock.shield")
                .font(.system(size: 10))
            Text("Selective activity data is sent to the server to help answer your questions with the LLM.")
                .font(.system(size: 11))
        }
        .foregroundStyle(.secondary)
        .frame(maxWidth: .infinity, alignment: .center)
        .multilineTextAlignment(.center)
    }

    // MARK: - Floating new-chat button

    private var newChatButton: some View {
        Button {
            viewModel.newChat()
            inputFocused = true
        } label: {
            Image(systemName: "square.and.pencil")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(PatchaTheme.primaryText(for: colorScheme))
                .frame(width: 34, height: 34)
                .background(
                    Circle().fill(PatchaTheme.tile(for: colorScheme))
                )
        }
        .buttonStyle(.plain)
        .help("New chat")
        .disabled(viewModel.isEmpty)
        .opacity(viewModel.isEmpty ? 0.4 : 1)
    }

    // MARK: - Empty state

    private static let suggestions = [
        "What was I working on this morning?",
        "Summarize my last 3 hours of activity",
        "When did I last touch the patcha project?",
    ]

    private var emptyState: some View {
        VStack(spacing: 0) {
            Spacer()
            Image(systemName: "sparkles")
                .font(.system(size: 34, weight: .light))
                .foregroundStyle(PatchaTheme.accent)
                .padding(.bottom, 16)
            Text("Talk to your activity")
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(PatchaTheme.primaryText(for: colorScheme))
            Text("patcha can search everything you've done on this Mac.")
                .font(.system(size: 13))
                .foregroundStyle(PatchaTheme.secondaryText(for: colorScheme))
                .padding(.top, 4)

            VStack(spacing: 8) {
                ForEach(Self.suggestions, id: \.self) { prompt in
                    Button {
                        viewModel.send(prompt)
                    } label: {
                        HStack {
                            Text(prompt)
                                .font(.system(size: 13))
                                .foregroundStyle(PatchaTheme.primaryText(for: colorScheme))
                            Spacer()
                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(PatchaTheme.accent)
                        }
                        .padding(.horizontal, 14)
                        .padding(.vertical, 11)
                        .background(
                            RoundedRectangle(cornerRadius: 11, style: .continuous)
                                .fill(PatchaTheme.tile(for: colorScheme))
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
            .frame(maxWidth: 420)
            .padding(.top, 28)

            Spacer()
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 28)
    }

    // MARK: - Transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    ForEach(viewModel.messages) { message in
                        MessageRow(message: message)
                            .id(message.id)
                    }
                    Color.clear.frame(height: 1).id(Self.bottomAnchor)
                }
                .padding(.horizontal, 28)
                .padding(.top, 22)
                .padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
            .background(ScrollerHider())
            .onChange(of: viewModel.messages.last?.text) {
                withAnimation(.easeOut(duration: 0.15)) {
                    proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
                }
            }
            .onChange(of: viewModel.messages.count) {
                withAnimation(.easeOut(duration: 0.15)) {
                    proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
                }
            }
        }
    }

    private static let bottomAnchor = "chat-bottom-anchor"

    // MARK: - Composer

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            ChatInputField(text: $viewModel.input, onSubmit: submit)
                .focused($inputFocused)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(
                    RoundedRectangle(cornerRadius: 13, style: .continuous)
                        .fill(PatchaTheme.tile(for: colorScheme))
                )

            if viewModel.isStreaming {
                circleButton(systemName: "stop.fill", tint: PatchaTheme.tile(for: colorScheme), iconColor: PatchaTheme.primaryText(for: colorScheme)) { viewModel.stop() }
            } else {
                circleButton(systemName: "arrow.up", tint: PatchaTheme.accent, iconColor: PatchaTheme.void, enabled: viewModel.canSend) {
                    submit()
                }
            }
        }
    }

    private func circleButton(systemName: String, tint: Color, iconColor: Color, enabled: Bool = true, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 14, weight: .bold))
                .foregroundStyle(iconColor)
                .frame(width: 36, height: 36)
                .background(Circle().fill(tint.opacity(enabled ? 1 : 0.4)))
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }

    private func submit() {
        guard viewModel.canSend else { return }
        viewModel.send()
    }
}

// MARK: - Message row

private struct MessageRow: View {
    let message: ChatMessage
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        switch message.role {
        case .user:
            HStack {
                Spacer(minLength: 60)
                Text(message.text)
                    .font(.system(size: 14))
                    .foregroundStyle(PatchaTheme.primaryText(for: colorScheme))
                    .textSelection(.enabled)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(PatchaTheme.tile(for: colorScheme))
                    )
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        case .assistant:
            VStack(alignment: .leading, spacing: 8) {
                if !message.toolEvents.isEmpty {
                    VStack(alignment: .leading, spacing: 5) {
                        ForEach(message.toolEvents) { tool in
                            ToolChip(tool: tool)
                        }
                    }
                }

                if message.text.isEmpty && message.isStreaming {
                    if message.toolEvents.isEmpty {
                        ThinkingIndicator()
                    }
                } else {
                    Text(attributed(message.text))
                        .font(.system(size: 14))
                        .foregroundStyle(PatchaTheme.primaryText(for: colorScheme))
                        .textSelection(.enabled)
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func attributed(_ text: String) -> AttributedString {
        (try? AttributedString(
            markdown: text,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        )) ?? AttributedString(text)
    }
}

private struct ToolChip: View {
    let tool: ToolEvent
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(spacing: 7) {
            if tool.done {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(PatchaTheme.accent)
            } else {
                ProgressView()
                    .controlSize(.small)
                    .scaleEffect(0.7)
                    .frame(width: 12, height: 12)
            }
            Text(tool.displayLabel)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(PatchaTheme.secondaryText(for: colorScheme))
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(
            Capsule(style: .continuous)
                .fill(PatchaTheme.tile(for: colorScheme))
        )
    }
}

private struct ThinkingIndicator: View {
    @State private var phase = 0.0

    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3) { i in
                Circle()
                    .fill(Color.secondary)
                    .frame(width: 6, height: 6)
                    .opacity(0.3 + 0.7 * abs(sin(phase + Double(i) * 0.6)))
            }
        }
        .onAppear {
            withAnimation(.linear(duration: 1).repeatForever(autoreverses: false)) {
                phase = .pi * 2
            }
        }
    }
}

// MARK: - Input field (multiline, Return-to-send, Shift+Return for newline)

private struct ChatInputField: View {
    @Binding var text: String
    let onSubmit: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ZStack(alignment: .topLeading) {
            if text.isEmpty {
                Text("Message patcha…")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
                    .padding(.top, 1)
                    .allowsHitTesting(false)
            }
            TextField("", text: $text, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.system(size: 14))
                .lineLimit(1...6)
                .onSubmit(onSubmit)
        }
    }
}
