import SwiftUI
import AppKit

struct AccessibilityBanner: View {
    @State private var granted: Bool = PermissionsManager.accessibilityGranted()
    @State private var pollTimer: Timer?
    @State private var activationObserver: NSObjectProtocol?

    var body: some View {
        Group {
            if granted {
                EmptyView()
            } else {
                HStack(spacing: 10) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundColor(.orange)
                    Text("Accessibility is off — Patcha can't read the focused window.")
                        .font(.system(size: 12.5))
                        .foregroundColor(.primary)
                    Spacer(minLength: 8)
                    Button("Open Settings") {
                        PermissionsManager.openAccessibilitySettings()
                    }
                    .buttonStyle(.borderless)
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundColor(PatchaTheme.accent)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.yellow.opacity(0.18))
                .overlay(
                    Rectangle()
                        .fill(PatchaTheme.softDivider)
                        .frame(height: 1),
                    alignment: .bottom
                )
            }
        }
        .onAppear {
            refresh()
            pollTimer?.invalidate()
            pollTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { _ in
                DispatchQueue.main.async { refresh() }
            }
            activationObserver = NotificationCenter.default.addObserver(
                forName: NSApplication.didBecomeActiveNotification,
                object: nil,
                queue: .main
            ) { _ in
                refresh()
            }
        }
        .onDisappear {
            pollTimer?.invalidate()
            pollTimer = nil
            if let token = activationObserver {
                NotificationCenter.default.removeObserver(token)
                activationObserver = nil
            }
        }
    }

    private func refresh() {
        let newValue = PermissionsManager.accessibilityGranted()
        if newValue != granted {
            granted = newValue
        }
    }
}
