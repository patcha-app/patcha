import ApplicationServices
import CoreGraphics

struct PermissionsManager {
    static func requestIfNeeded() {
        requestAccessibility()
        requestScreenRecording()
    }

    private static func requestAccessibility() {
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        let opts = [key: true] as CFDictionary
        AXIsProcessTrustedWithOptions(opts)
    }

    private static func requestScreenRecording() {
        CGRequestScreenCaptureAccess()
    }
}
