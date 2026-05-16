import Foundation

enum SettingsSection: CaseIterable, Hashable {
    case permissions, general, memories, modelPreference, account

    var title: String {
        switch self {
        case .permissions:     return "App Permissions"
        case .general:         return "General"
        case .memories:        return "Memories"
        case .modelPreference: return "Model Preference"
        case .account:         return "Account"
        }
    }

    var systemImage: String {
        switch self {
        case .permissions:     return "shield"
        case .general:         return "gearshape"
        case .memories:        return "clock"
        case .modelPreference: return "cpu"
        case .account:         return "person.circle"
        }
    }
}
