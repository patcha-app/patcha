import Foundation

enum SettingsSection: CaseIterable, Hashable {
    case chat, permissions, general, timeline, memories, modelPreference, integrations, account

    var title: String {
        switch self {
        case .chat:            return "Chat"
        case .permissions:     return "Permissions"
        case .general:         return "General"
        case .timeline:        return "Timeline"
        case .memories:        return "Memories"
        case .modelPreference: return "Model Preference"
        case .integrations:    return "Integrations"
        case .account:         return "Account"
        }
    }

    var systemImage: String {
        switch self {
        case .chat:            return "bubble.left.and.bubble.right"
        case .permissions:     return "shield"
        case .general:         return "gearshape"
        case .timeline:        return "calendar.day.timeline.left"
        case .memories:        return "clock"
        case .modelPreference: return "cpu"
        case .integrations:    return "link"
        case .account:         return "person.circle"
        }
    }
}
