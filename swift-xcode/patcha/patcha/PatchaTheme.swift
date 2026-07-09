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
    // Phosphor terminal palette
    static let phosphorMint = Color(hex: "ddffdc")
    static let reactorGreen = Color(hex: "7fee64")
    static let softGlow     = Color(hex: "c8f9b6")
    static let paleMist     = Color(hex: "def0dd")
    static let sageTint     = Color(hex: "aed2a4")
    static let void         = Color(hex: "000000")
    static let carbon       = Color(hex: "212525")
    static let moss         = Color(hex: "3e4a3c")
    static let fern         = Color(hex: "485346")
    static let lichen       = Color(hex: "677d64")
    static let slate        = Color(hex: "697368")
    static let stone        = Color(hex: "859085")
    static let bone         = Color(hex: "ffffff")

    static let accent = reactorGreen
    static let softDivider = Color.primary.opacity(0.08)

    static func bg(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? void : Color(hex: "F7F8F5")
    }

    static func surface(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? carbon : Color.white
    }

    static func sidebar(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? void : Color(hex: "F0F0F2")
    }

    static func hairline(for colorScheme: ColorScheme) -> Color {
        Color.primary.opacity(colorScheme == .dark ? 0.12 : 0.08)
    }

    /// Elevated tile / chip fill — Moss on dark, faint ink on light.
    static func tile(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? moss.opacity(0.55) : Color.primary.opacity(0.05)
    }

    static func selectedOverlay(for colorScheme: ColorScheme) -> Color {
        reactorGreen.opacity(colorScheme == .dark ? 0.18 : 0.15)
    }

    /// Primary body text — luminescent Phosphor Mint on dark, near-black on light.
    static func primaryText(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? phosphorMint : Color(hex: "0F0F11")
    }

    /// Muted/secondary text — Lichen on dark.
    static func secondaryText(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? lichen : Color.secondary
    }
}

struct SectionHeader: View {
    let title: String
    var body: some View {
        Text(title)
            .font(.pSectionLabel)
            .foregroundStyle(.primary)
    }
}

struct AccentButtonStyle: ButtonStyle {
    var isDisabled: Bool = false
    var fullWidth: Bool = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.pBodyStrong)
            .foregroundColor(PatchaTheme.void)
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

struct GhostButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.pBodyStrong)
            .foregroundColor(.primary)
            .padding(.vertical, 9)
            .padding(.horizontal, 16)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(Color.primary.opacity(configuration.isPressed ? 0.10 : 0.06))
            )
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

extension Font {
    static let pCaption       = Font.system(size: 10, weight: .regular)   // Caption 1
    static let pCaptionStrong = Font.system(size: 10, weight: .medium)    // Caption 2
    static let pBody          = Font.system(size: 13, weight: .regular)   // Body
    static let pBodyStrong    = Font.system(size: 13, weight: .semibold)  // Body emphasized
    static let pCallout       = Font.system(size: 12, weight: .regular)   // Callout
    static let pCalloutStrong = Font.system(size: 12, weight: .semibold)  // Callout emphasized
    static let pSubheadline   = Font.system(size: 11, weight: .regular)   // Subheadline
    static let pHeadline      = Font.system(size: 13, weight: .bold)      // Headline
    static let pTitle         = Font.system(size: 22, weight: .regular)   // Title 1
    static let pHeroMark      = Font.system(size: 56, weight: .heavy)

    static let pSectionLabel  = Font.system(size: 11, weight: .semibold)  // Subheadline emphasized

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
