import Combine
import Foundation

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var input: String = ""
    @Published var isStreaming = false
    @Published var errorText: String?

    private let port: Int
    private let store: SettingsStore
    private let service = ChatService()
    private var streamTask: Task<Void, Never>?

    init(port: Int, store: SettingsStore) {
        self.port = port
        self.store = store
    }

    var canSend: Bool {
        !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isStreaming
    }

    var isEmpty: Bool { messages.isEmpty }

    func send(_ overrideText: String? = nil) {
        let text = (overrideText ?? input).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isStreaming else { return }

        input = ""
        errorText = nil
        messages.append(ChatMessage(role: .user, text: text))

        let transcript = wireMessages()
        messages.append(ChatMessage(role: .assistant, text: "", isStreaming: true))
        let assistantIndex = messages.count - 1
        isStreaming = true

        let backend = store.chatBackend
        streamTask = Task {
            do {
                for try await event in service.stream(port: port, backend: backend, messages: transcript) {
                    apply(event, at: assistantIndex)
                }
            } catch {
                applyError(error.localizedDescription, at: assistantIndex)
            }
            finishStreaming(at: assistantIndex)
        }
    }

    func newChat() {
        streamTask?.cancel()
        streamTask = nil
        isStreaming = false
        errorText = nil
        messages.removeAll()
        input = ""
    }

    func stop() {
        streamTask?.cancel()
        streamTask = nil
        if let i = messages.indices.last {
            finishStreaming(at: i)
        }
    }

    // MARK: - Helpers

    private func wireMessages() -> [[String: String]] {
        messages
            .filter { !($0.role == .assistant && $0.text.isEmpty) }
            .map { ["role": $0.role.rawValue, "content": $0.text] }
    }

    private func apply(_ event: ChatStreamEvent, at index: Int) {
        guard messages.indices.contains(index) else { return }
        switch event {
        case .tool(let name, let done):
            if let existing = messages[index].toolEvents.lastIndex(where: { $0.name == name && !$0.done }) {
                messages[index].toolEvents[existing].done = done
            } else {
                messages[index].toolEvents.append(ToolEvent(name: name, done: done))
            }
        case .token(let text):
            messages[index].text += text
        case .error(let message):
            applyError(message, at: index)
        case .done:
            break
        }
    }

    private func applyError(_ message: String, at index: Int) {
        errorText = message
        guard messages.indices.contains(index) else { return }
        if messages[index].text.isEmpty {
            messages[index].text = "I couldn't answer that — \(message)"
        }
    }

    private func finishStreaming(at index: Int) {
        isStreaming = false
        guard messages.indices.contains(index) else { return }
        messages[index].isStreaming = false
        for i in messages[index].toolEvents.indices {
            messages[index].toolEvents[i].done = true
        }
    }
}
