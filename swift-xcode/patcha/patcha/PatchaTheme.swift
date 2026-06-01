import SwiftUI

extension Color {
    init(hex: String) {
        let cleaned = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        var int: UInt64 = 0
        Scanner(string: cleaned).scanHexInt64(&int)
        let r, g, b, a: UInt64
        switch cleaned.count {
        case 6:
            (r, g, b, a) = ((int >> 16) & 0xFF, (int >> 8) & 0xFF, int & 0xFF, 255)
        case 8:
            (r, g, b, a) = ((int >> 24) & 0xFF, (int >> 16) & 0xFF, (int >> 8) & 0xFF, int & 0xFF)
        default:
            (r, g, b, a) = (255, 255, 255, 255)
        }
        self.init(
            .sRGB,
            red: Double(r) / 255,
            green: Double(g) / 255,
            blue: Double(b) / 255,
            opacity: Double(a) / 255
        )
    }
}

enum PatchaTheme {
    static let accent = Color(hex: "00CE93")
    static let softDivider = Color.primary.opacity(0.08)

    static func bg(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .black : .white
    }
}

struct SectionHeader: View {
    let title: String
    var body: some View {
        Text(title.uppercased())
            .font(.pSectionLabel)
            .tracking(1.2)
            .foregroundStyle(.primary)
    }
}

struct AccentButtonStyle: ButtonStyle {
    var isDisabled: Bool = false
    var fullWidth: Bool = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.pBodyStrong)
            .foregroundColor(.black)
            .frame(maxWidth: fullWidth ? .infinity : nil)
            .padding(.vertical, 9)
            .padding(.horizontal, fullWidth ? 0 : 16)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(PatchaTheme.accent.opacity(isDisabled ? 0.4 : configuration.isPressed ? 0.8 : 1.0))
            )
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

extension Font {
    static let pCaption       = Font.system(size: 11, weight: .regular)
    static let pCaptionStrong = Font.system(size: 11, weight: .semibold)
    static let pBody          = Font.system(size: 13, weight: .regular)
    static let pBodyStrong    = Font.system(size: 13, weight: .semibold)
    static let pCallout       = Font.system(size: 14, weight: .regular)
    static let pCalloutStrong = Font.system(size: 14, weight: .semibold)
    static let pSubheadline   = Font.system(size: 15, weight: .regular)
    static let pHeadline      = Font.system(size: 16, weight: .semibold)
    static let pTitle         = Font.system(size: 22, weight: .bold)
    static let pHeroMark      = Font.system(size: 56, weight: .heavy)

    static let pSectionLabel  = Font.system(size: 11, weight: .semibold)

    static let pEyebrow       = Font.system(size: 12, weight: .regular)
    static let pBrandmark     = Font.system(size: 16, weight: .regular)
    static let pNumeral       = Font.system(size: 16, weight: .regular).italic()
    static let pNoteItalic    = Font.system(size: 13, weight: .regular).italic()
    static let pDisplay       = Font.system(size: 44, weight: .regular)
    static let pDisplayItalic = Font.system(size: 44, weight: .regular).italic()

    static let pIconSmall     = Font.system(size: 13, weight: .regular)
    static let pIconMedium    = Font.system(size: 14, weight: .regular)
    static let pIconLarge     = Font.system(size: 16, weight: .regular)
    static let pIconXLarge    = Font.system(size: 18, weight: .regular)
    static let pIconAvatar    = Font.system(size: 36, weight: .regular)
    static let pIconEmpty     = Font.system(size: 40, weight: .regular)
}
