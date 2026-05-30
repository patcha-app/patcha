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
    static let pCaption       = Font.custom("Britanica-Bold", size: 11)
    static let pCaptionStrong = Font.custom("Britanica-Bold", size: 11)
    static let pBody          = Font.custom("Britanica-Bold", size: 13)
    static let pBodyStrong    = Font.custom("Britanica-Bold", size: 13)
    static let pCallout       = Font.custom("Britanica-Bold", size: 14)
    static let pCalloutStrong = Font.custom("Britanica-Bold", size: 14)
    static let pSubheadline   = Font.custom("Britanica-Bold", size: 15)
    static let pHeadline      = Font.custom("Britanica-Bold", size: 16)
    static let pTitle         = Font.custom("Britanica-ExtraBoldSemiExpanded", size: 22)
    static let pHeroMark      = Font.custom("Britanica-ExtraBoldSemiExpanded", size: 56)

    static let pSectionLabel  = Font.custom("Britanica-BoldSemiExpanded", size: 11)

    static let pEyebrow       = Font.custom("InstrumentSerif-Regular", size: 12)
    static let pBrandmark     = Font.custom("InstrumentSerif-Regular", size: 16)
    static let pNumeral       = Font.custom("InstrumentSerif-Italic",  size: 16)
    static let pNoteItalic    = Font.custom("InstrumentSerif-Italic",  size: 13)
    static let pDisplay       = Font.custom("InstrumentSerif-Regular", size: 44)
    static let pDisplayItalic = Font.custom("InstrumentSerif-Italic",  size: 44)

    static let pIconSmall     = Font.custom("Britanica-Bold", size: 13)
    static let pIconMedium    = Font.custom("Britanica-Bold", size: 14)
    static let pIconLarge     = Font.custom("Britanica-Bold", size: 16)
    static let pIconXLarge    = Font.custom("Britanica-Bold", size: 18)
    static let pIconAvatar    = Font.custom("Britanica-Bold", size: 36)
    static let pIconEmpty     = Font.custom("Britanica-Bold", size: 40)
}
