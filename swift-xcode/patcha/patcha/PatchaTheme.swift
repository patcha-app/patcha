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
            .font(.britanicaSemiExpandedBold(11))
            .tracking(1.2)
            .foregroundStyle(.primary)
    }
}

struct AccentButtonStyle: ButtonStyle {
    var isDisabled: Bool = false
    var fullWidth: Bool = false
    @Environment(\.colorScheme) private var colorScheme

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.britanica(13))
            .foregroundColor(PatchaTheme.bg(for: colorScheme))
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
    static func britanica(_ size: CGFloat) -> Font {
        .custom("Britanica-Bold", size: size)
    }

    static func britanicaSemiExpandedBold(_ size: CGFloat) -> Font {
        .custom("Britanica-BoldSemiExpanded", size: size)
    }

    static func instrumentSerif(_ size: CGFloat) -> Font {
        .custom("InstrumentSerif-Regular", size: size)
    }

    static func instrumentSerifItalic(_ size: CGFloat) -> Font {
        .custom("InstrumentSerif-Italic", size: size)
    }
}
