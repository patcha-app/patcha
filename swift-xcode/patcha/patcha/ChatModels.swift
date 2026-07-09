import Foundation

enum ChatRole: String {
    case user
    case assistant
}

enum ChatBackend: String, CaseIterable, Identifiable {
    case api
    case claude
    case codex

    var id: String { rawValue }

    var title: String {
        switch self {
        case .api:    return "patcha (built-in)"
        case .claude: return "Claude Code"
        case .codex:  return "Codex"
        }
    }

    var subtitle: String {
        switch self {
        case .api:    return "Runs in the cloud via patcha. No setup — works out of the box."
        case .claude: return "Uses the local Claude Code CLI and your Claude plan. Stronger answers."
        case .codex:  return "Uses the local Codex CLI and your OpenAI account."
        }
    }

    var systemImage: String {
        switch self {
        case .api:    return "cloud"
        case .claude: return "terminal"
        case .codex:  return "chevron.left.forwardslash.chevron.right"
        }
    }

    /// The three backends require a local CLI except the built-in one.
    var requiresCLI: Bool { self != .api }
}

struct ToolEvent: Identifiable, Equatable {
    let id = UUID()
    let name: String
    var done: Bool

    var displayLabel: String {
        switch name {
        case "get_working_memory":   return done ? "Read working memory" : "Reading working memory"
        case "search_activity":      return done ? "Searched activity" : "Searching activity"
        case "get_recent_activity":  return done ? "Read recent activity" : "Reading recent activity"
        case "get_activity_context": return done ? "Looked up context" : "Looking up context"
        case "get_session":          return done ? "Loaded session" : "Loading session"
        case "find_connected":       return done ? "Found connections" : "Finding connections"
        default:                     return done ? "Done" : "Working"
        }
    }
}

struct ChatMessage: Identifiable {
    let id = UUID()
    let role: ChatRole
    var text: String
    var toolEvents: [ToolEvent] = []
    var isStreaming: Bool = false
}

enum ChatStreamEvent {
    case tool(name: String, done: Bool)
    case token(String)
    case done
    case error(String)
}
