import Foundation

enum SettingsSection: CaseIterable, Hashable {
    case permissions, general, memories, modelPreference, integrations, account

    var title: String {
        switch self {
        case .permissions:     return "Permissions"
        case .general:         return "General"
        case .memories:        return "Memories"
        case .modelPreference: return "Model Preference"
        case .integrations:    return "Integrations"
        case .account:         return "Account"
        }
    }

    var systemImage: String {
        switch self {
        case .permissions:     return "shield.fill"
        case .general:         return "gearshape.fill"
        case .memories:        return "clock.fill"
        case .modelPreference: return "cpu.fill"
        case .integrations:    return "link"
        case .account:         return "person.circle.fill"
        }
    }
}
