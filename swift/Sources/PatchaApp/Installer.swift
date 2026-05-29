import Foundation
import ServiceManagement

enum Installer {
    private static let installedKey = "cliInstalled"

    static func installIfNeeded() {
        guard !UserDefaults.standard.bool(forKey: installedKey) else { return }

        do {
            try SMAppService.mainApp.register()
            NSLog("[Installer] registered as login item")
        } catch {
            NSLog("[Installer] failed to register login item: %@", error.localizedDescription)
        }

        let fm = FileManager.default
        let home = fm.homeDirectoryForCurrentUser.path
        let binDir = "\(home)/.local/bin"

        do {
            try fm.createDirectory(atPath: binDir, withIntermediateDirectories: true)
        } catch {
            NSLog("[Installer] failed to create ~/.local/bin: %@", error.localizedDescription)
            return
        }

        guard let resourceURL = Bundle.main.resourceURL else { return }

        for binary in ["patcha", "patcha-mcp"] {
            let src = resourceURL.appendingPathComponent(binary).path
            let dst = "\(binDir)/\(binary)"

            guard fm.isExecutableFile(atPath: src) else {
                NSLog("[Installer] binary not found in bundle: %@", binary)
                continue
            }

            do {
                if fm.fileExists(atPath: dst) {
                    try fm.removeItem(atPath: dst)
                }
                try fm.copyItem(atPath: src, toPath: dst)
                try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: dst)
                NSLog("[Installer] installed %@ -> %@", binary, dst)
            } catch {
                NSLog("[Installer] failed to install %@: %@", binary, error.localizedDescription)
                return
            }
        }

        UserDefaults.standard.set(true, forKey: installedKey)
    }
}
