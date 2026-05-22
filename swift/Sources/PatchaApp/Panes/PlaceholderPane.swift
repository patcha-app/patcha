import SwiftUI

private struct PlaceholderPane_Previews: PreviewProvider {
    static var previews: some View {
        PlaceholderPane(section: .memories)
            .frame(width: 480, height: 300)
    }
}

struct PlaceholderPane: View {
    let section: SettingsSection

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: section.systemImage)
                .font(.system(size: 40))
                .foregroundStyle(.tertiary)
            Text("Coming soon")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
