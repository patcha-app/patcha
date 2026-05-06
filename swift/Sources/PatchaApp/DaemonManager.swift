import AppKit
import Combine
import Foundation

enum DaemonStatus: Equatable {
    case stopped
    case starting
    case running
    case restarting(attempt: Int)
    case failed
}

class DaemonManager: ObservableObject {
    @Published var status: DaemonStatus = .stopped

    private var process: Process?
    private var processSource: DispatchSourceProcess?
    private var stabilityTimer: Timer?
    private var restartCount = 0
    private let maxRestarts = 5
    private let backoffDelays: [Double] = [5, 10, 20, 40, 80]
    private let stabilityWindow: TimeInterval = 600

    func start() {
        guard case .stopped = status else { return }
        launchProcess()
    }

    func restart() {
        stop()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
            self?.restartCount = 0
            self?.status = .stopped
            self?.launchProcess()
        }
    }

    func stop() {
        stabilityTimer?.invalidate()
        stabilityTimer = nil
        processSource?.cancel()
        processSource = nil

        guard let proc = process, proc.isRunning else {
            process = nil
            status = .stopped
            return
        }

        proc.terminate()

        let deadline = DispatchTime.now() + 5
        DispatchQueue.global().asyncAfter(deadline: deadline) { [weak self] in
            if let proc = self?.process, proc.isRunning {
                kill(proc.processIdentifier, SIGKILL)
            }
            DispatchQueue.main.async {
                self?.process = nil
                self?.status = .stopped
            }
        }
    }

    private func launchProcess() {
        let (execURL, args, workDir) = resolveDaemonPath()

        guard FileManager.default.isExecutableFile(atPath: execURL.path) else {
            NSLog("[DaemonManager] executable not found: %@", execURL.path)
            scheduleRestart()
            return
        }

        let proc = Process()
        proc.executableURL = execURL
        proc.arguments = args
        if let workDir {
            proc.currentDirectoryURL = URL(fileURLWithPath: workDir)
        }

        var env = ProcessInfo.processInfo.environment
        let home = env["HOME"] ?? NSHomeDirectory()
        let extraPaths = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        env["PATH"] = [env["PATH"], extraPaths].compactMap { $0 }.joined(separator: ":")
        proc.environment = env

        let errPipe = Pipe()
        proc.standardError = errPipe
        errPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if !data.isEmpty, let line = String(data: data, encoding: .utf8) {
                NSLog("[patcha-daemon] %@", line)
            }
        }

        do {
            status = .starting
            try proc.run()
        } catch {
            NSLog("[DaemonManager] launch failed: %@", error.localizedDescription)
            DispatchQueue.main.async { [weak self] in
                self?.scheduleRestart()
            }
            return
        }

        process = proc

        let pid = proc.processIdentifier
        let source = DispatchSource.makeProcessSource(identifier: pid, eventMask: .exit, queue: .main)
        source.setEventHandler { [weak self] in
            self?.handleProcessExit()
        }
        source.resume()
        processSource = source

        status = .running
        startStabilityTimer()
    }

    private func handleProcessExit() {
        processSource?.cancel()
        processSource = nil
        process = nil
        stabilityTimer?.invalidate()
        stabilityTimer = nil
        scheduleRestart()
    }

    private func scheduleRestart() {
        guard restartCount < maxRestarts else {
            status = .failed
            return
        }

        let delay = backoffDelays[min(restartCount, backoffDelays.count - 1)]
        restartCount += 1
        status = .restarting(attempt: restartCount)

        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.launchProcess()
        }
    }

    private func startStabilityTimer() {
        stabilityTimer?.invalidate()
        stabilityTimer = Timer.scheduledTimer(withTimeInterval: stabilityWindow, repeats: false) { [weak self] _ in
            self?.restartCount = 0
        }
    }

    private func resolveDaemonPath() -> (URL, [String], String?) {
        if let bundleResource = Bundle.main.resourceURL {
            let bundledBinary = bundleResource.appendingPathComponent("patcha")
            if FileManager.default.isExecutableFile(atPath: bundledBinary.path) {
                NSLog("[DaemonManager] using bundled binary: %@", bundledBinary.path)
                return (bundledBinary, [], nil)
            }
        }

        if let envPath = ProcessInfo.processInfo.environment["PATCHA_DAEMON_PATH"] {
            let url = URL(fileURLWithPath: envPath)
            if FileManager.default.isExecutableFile(atPath: url.path) {
                NSLog("[DaemonManager] using PATCHA_DAEMON_PATH: %@", envPath)
                return (url, [], nil)
            }
        }

        let projectDir = detectProjectDir()
        guard let uv = findExecutable("uv") else {
            NSLog("[DaemonManager] uv not found in PATH — daemon cannot start")
            DispatchQueue.main.async { DaemonManager.showUvMissingAlert() }
            status = .failed
            return (URL(fileURLWithPath: "/usr/bin/false"), [], nil)
        }

        NSLog("[DaemonManager] dev mode: uv=%@ project=%@", uv, projectDir)
        return (
            URL(fileURLWithPath: uv),
            ["run", "python", "main.py"],
            projectDir
        )
    }

    private func findExecutable(_ name: String) -> String? {
        let env = ProcessInfo.processInfo.environment
        let home = env["HOME"] ?? NSHomeDirectory()
        var dirs = (env["PATH"] ?? "").split(separator: ":").map(String.init)
        dirs += ["\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]
        for dir in dirs {
            let path = "\(dir)/\(name)"
            if FileManager.default.isExecutableFile(atPath: path) {
                return path
            }
        }
        return nil
    }

    private static func showUvMissingAlert() {
        let alert = NSAlert()
        alert.messageText = "uv not found"
        alert.informativeText = """
            Patcha requires uv to run the Python daemon in development mode.

            Install uv with:
              curl -LsSf https://astral.sh/uv/install.sh | sh

            Then relaunch Patcha.
            """
        alert.alertStyle = .critical
        alert.addButton(withTitle: "Open uv Installation Page")
        alert.addButton(withTitle: "Quit")
        NSApp.activate(ignoringOtherApps: true)
        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            NSWorkspace.shared.open(URL(string: "https://docs.astral.sh/uv/getting-started/installation/")!)
        }
        NSApp.terminate(nil)
    }

    private func detectProjectDir() -> String {
        if let envDir = ProcessInfo.processInfo.environment["PATCHA_PROJECT_DIR"] {
            return envDir
        }

        // In .app bundle: bundle lives at <project>/dist/Patcha.app — two levels up is project root.
        let bundlePath = Bundle.main.bundlePath
        let distDir = (bundlePath as NSString).deletingLastPathComponent
        let candidate = (distDir as NSString).deletingLastPathComponent
        if FileManager.default.fileExists(atPath: "\(candidate)/main.py") {
            return candidate
        }

        // In dev mode (swift run): walk up from the executable to find main.py.
        if let execPath = Bundle.main.executablePath {
            var dir = (execPath as NSString).deletingLastPathComponent
            for _ in 0..<10 {
                if FileManager.default.fileExists(atPath: "\(dir)/main.py") {
                    return dir
                }
                let parent = (dir as NSString).deletingLastPathComponent
                if parent == dir { break }
                dir = parent
            }
        }

        NSLog("[DaemonManager] could not detect project dir; set PATCHA_PROJECT_DIR env var")
        return (Bundle.main.bundlePath as NSString).deletingLastPathComponent
    }
}
