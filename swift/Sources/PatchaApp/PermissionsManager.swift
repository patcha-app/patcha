import ApplicationServices
import CoreGraphics
import Foundation

struct PermissionsManager {
    private static let accessibilityPromptedKey = "hasPromptedAccessibility"
    private static let screenRecordingPromptedKey = "hasPromptedScreenRecording"

    static func requestIfNeeded() {
        requestAccessibility()
        requestScreenRecording()
    }

    static func accessibilityGranted() -> Bool {
        AXIsProcessTrusted()
    }

    static func screenRecordingGranted() -> Bool {
        CGPreflightScreenCaptureAccess()
    }

    private static func requestAccessibility() {
        guard !AXIsProcessTrusted() else { return }
        guard !UserDefaults.standard.bool(forKey: accessibilityPromptedKey) else { return }
        UserDefaults.standard.set(true, forKey: accessibilityPromptedKey)
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
    }

    private static func requestScreenRecording() {
        guard !CGPreflightScreenCaptureAccess() else { return }
        guard !UserDefaults.standard.bool(forKey: screenRecordingPromptedKey) else { return }
        UserDefaults.standard.set(true, forKey: screenRecordingPromptedKey)
        CGRequestScreenCaptureAccess()
    }

    static func resetPromptFlags() {
        UserDefaults.standard.removeObject(forKey: accessibilityPromptedKey)
        UserDefaults.standard.removeObject(forKey: screenRecordingPromptedKey)
    }
}
