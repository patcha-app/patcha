import ApplicationServices
import AppKit
import CoreGraphics
import Foundation

struct PermissionStatus {
    var accessibility: Bool
    var screenRecording: Bool
    var fullDiskAccess: Bool
    var allGranted: Bool { accessibility && screenRecording && fullDiskAccess }
}

struct PermissionsManager {
    private static let accessibilityPromptedKey = "hasPromptedAccessibility"
    private static let screenRecordingPromptedKey = "hasPromptedScreenRecording"
    private static let fdaProbingArmedKey = "fdaProbingArmed"
    private static let fdaPreviouslyGrantedKey = "fdaPreviouslyGranted"

    static func requestIfNeeded() {
        requestAccessibility()
        requestScreenRecording()
    }

    static func snapshot() -> PermissionStatus {
        PermissionStatus(
            accessibility: accessibilityGranted(),
            screenRecording: screenRecordingGranted(),
            fullDiskAccess: fullDiskAccessGranted()
        )
    }

    static func accessibilityGranted() -> Bool {
        AXIsProcessTrusted()
    }

    static func screenRecordingGranted() -> Bool {
        CGPreflightScreenCaptureAccess()
    }

    static func fullDiskAccessGranted() -> Bool {
        guard fdaProbingArmed() else { return false }
        let home = NSHomeDirectory() as NSString
        let candidates = [
            home.appendingPathComponent("Library/Safari/Bookmarks.plist"),
            home.appendingPathComponent("Library/Safari/CloudTabs.db"),
            home.appendingPathComponent("Library/Mail"),
            home.appendingPathComponent("Library/Messages"),
        ]
        for path in candidates {
            guard FileManager.default.fileExists(atPath: path) else { continue }
            let url = URL(fileURLWithPath: path)
            var isDir: ObjCBool = false
            FileManager.default.fileExists(atPath: path, isDirectory: &isDir)
            if isDir.boolValue {
                if (try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)) != nil {
                    rememberFDAGranted()
                    return true
                }
            } else {
                if (try? Data(contentsOf: url, options: .alwaysMapped)) != nil {
                    rememberFDAGranted()
                    return true
                }
            }
            return false
        }
        return false
    }

    static func fdaProbingArmed() -> Bool {
        UserDefaults.standard.bool(forKey: fdaProbingArmedKey)
            || UserDefaults.standard.bool(forKey: fdaPreviouslyGrantedKey)
    }

    static func armFDAProbing() {
        UserDefaults.standard.set(true, forKey: fdaProbingArmedKey)
    }

    private static func rememberFDAGranted() {
        UserDefaults.standard.set(true, forKey: fdaPreviouslyGrantedKey)
    }

    static func requestAccessibility() {
        guard !AXIsProcessTrusted() else { return }
        UserDefaults.standard.set(true, forKey: accessibilityPromptedKey)
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
    }

    static func requestScreenRecording() {
        guard !CGPreflightScreenCaptureAccess() else { return }
        UserDefaults.standard.set(true, forKey: screenRecordingPromptedKey)
        CGRequestScreenCaptureAccess()
    }

    static func openAccessibilitySettings() {
        openSettings("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
    }

    static func openScreenRecordingSettings() {
        openSettings("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")
    }

    static func openFullDiskAccessSettings() {
        openSettings("x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")
    }

    private static func openSettings(_ urlString: String) {
        if let url = URL(string: urlString) {
            NSWorkspace.shared.open(url)
        }
    }

    static func resetPromptFlags() {
        UserDefaults.standard.removeObject(forKey: accessibilityPromptedKey)
        UserDefaults.standard.removeObject(forKey: screenRecordingPromptedKey)
    }
}
