import Foundation

enum ChatServiceError: LocalizedError {
    case noPort
    case badResponse
    case server(String)

    var errorDescription: String? {
        switch self {
        case .noPort:
            return "The patcha server isn't running yet. Try again in a moment."
        case .badResponse:
            return "Couldn't reach the patcha server."
        case .server(let message):
            return message
        }
    }
}

struct ChatService {
    /// Open an SSE stream against the local server's /api/chat endpoint.
    /// `messages` is the OpenAI-style transcript: [["role": ..., "content": ...]].
    func stream(port: Int, backend: String, messages: [[String: String]]) -> AsyncThrowingStream<ChatStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    guard port > 0,
                          let url = URL(string: "http://127.0.0.1:\(port)/api/chat") else {
                        throw ChatServiceError.noPort
                    }

                    var request = URLRequest(url: url)
                    request.httpMethod = "POST"
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    request.httpBody = try JSONSerialization.data(
                        withJSONObject: ["messages": messages, "backend": backend]
                    )

                    let (bytes, response) = try await URLSession.shared.bytes(for: request)

                    guard let http = response as? HTTPURLResponse else {
                        throw ChatServiceError.badResponse
                    }
                    guard http.statusCode == 200 else {
                        throw ChatServiceError.server("Server returned status \(http.statusCode).")
                    }

                    var eventName = "message"
                    for try await line in bytes.lines {
                        if line.isEmpty {
                            eventName = "message"
                            continue
                        }
                        if line.hasPrefix("event:") {
                            eventName = line.dropFirst("event:".count)
                                .trimmingCharacters(in: .whitespaces)
                        } else if line.hasPrefix("data:") {
                            let payload = line.dropFirst("data:".count)
                                .trimmingCharacters(in: .whitespaces)
                            if let event = Self.decode(event: eventName, payload: payload) {
                                continuation.yield(event)
                                if case .done = event { break }
                            }
                        }
                    }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private static func decode(event: String, payload: String) -> ChatStreamEvent? {
        let obj = (try? JSONSerialization.jsonObject(with: Data(payload.utf8))) as? [String: Any]
        switch event {
        case "tool":
            let name = obj?["name"] as? String ?? ""
            let done = (obj?["status"] as? String) == "done"
            return .tool(name: name, done: done)
        case "token":
            return .token(obj?["text"] as? String ?? "")
        case "error":
            return .error(obj?["message"] as? String ?? "Something went wrong.")
        case "done":
            return .done
        default:
            return nil
        }
    }
}
