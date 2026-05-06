import ApplicationServices
import CoreGraphics

struct PermissionsManager {
    static func requestIfNeeded() {
        requestAccessibility()
        requestScreenRecording()
    }

    private static func requestAccessibility() {
        guard !AXIsProcessTrusted() else { return }
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        let opts = [key: true] as CFDictionary
        AXIsProcessTrustedWithOptions(opts)
    }

    private static func requestScreenRecording() {
        guard !CGPreflightScreenCaptureAccess() else { return }
        CGRequestScreenCaptureAccess()
    }
}
