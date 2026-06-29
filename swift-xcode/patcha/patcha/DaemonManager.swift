import AppKit
import Combine
import Foundation

enum DaemonStatus: Equatable {
    case stopped
    case starting
    case running
    case paused
    case restarting(attempt: Int)
    case failed
}

@MainActor class DaemonManager: ObservableObject {
    @Published var status: DaemonStatus = .stopped
    @Published var pausedUntil: Date? = nil
    var authToken: String?

    private var process: Process?
    private var processSource: DispatchSourceProcess?
    private var stabilityTimer: Timer?
    private var pauseWorkItem: DispatchWorkItem?
    private var restartCount = 0
    private let maxRestarts = 5
    private let backoffDelays: [Double] = [5, 10, 20, 40, 80]
    private let stabilityWindow: TimeInterval = 600

    func pause(until date: Date) {
        guard case .running = status else { return }
        guard let proc = process, proc.isRunning else { return }
        pauseWorkItem?.cancel()
        kill(proc.processIdentifier, SIGSTOP)
        pausedUntil = date
        status = .paused
        let delay = max(0, date.timeIntervalSinceNow)
        let item = DispatchWorkItem { [weak self] in self?.resume() }
        pauseWorkItem = item
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: item)
    }

    func resume() {
        guard case .paused = status else { return }
        pauseWorkItem?.cancel()
        pauseWorkItem = nil
        guard let proc = process, proc.isRunning else {
            pausedUntil = nil
            status = .stopped
            scheduleRestart()
            return
        }
        kill(proc.processIdentifier, SIGCONT)
        pausedUntil = nil
        status = .running
        startStabilityTimer()
    }

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
        pauseWorkItem?.cancel()
        pauseWorkItem = nil
        pausedUntil = nil
        stabilityTimer?.invalidate()
        stabilityTimer = nil
        processSource?.cancel()
        processSource = nil

        guard let proc = process, proc.isRunning else {
            process = nil
            status = .stopped
            return
        }

        let pid = proc.processIdentifier
        kill(pid, SIGCONT)
        proc.terminate()
        process = nil
        status = .stopped

        Task.detached {
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            if kill(pid, 0) == 0 {
                kill(pid, SIGKILL)
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
        env["PATCHA_ENV"] = "production"
        if let token = authToken {
            env["PATCHA_ACCESS_TOKEN"] = token
        }
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
            scheduleRestart()
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
        pauseWorkItem?.cancel()
        pauseWorkItem = nil
        pausedUntil = nil
        let exitCode = process?.terminationStatus ?? -1
        let exitReason = process?.terminationReason ?? .exit

        processSource?.cancel()
        processSource = nil
        process = nil
        stabilityTimer?.invalidate()
        stabilityTimer = nil

        // Clean exit (code 0, normal reason) means the daemon stopped on its own
        // intentionally — don't restart endlessly; surface it as stopped.
        if exitCode == 0 && exitReason == .exit {
            NSLog("[DaemonManager] daemon exited cleanly (code 0) — not restarting")
            status = .stopped
            return
        }

        // Exit code 2 signals the daemon is not authenticated.
        if exitCode == 2 {
            NSLog("[DaemonManager] daemon exited with auth error (code 2)")
            status = .failed
            DaemonManager.showConfigErrorAlert()
            return
        }

        scheduleRestart()
    }

    @MainActor private static func showConfigErrorAlert() {
        let alert = NSAlert()
        alert.messageText = "Patcha configuration error"
        alert.informativeText = """
            The daemon exited unexpectedly. Check that the project directory is correct \
            and all dependencies are installed, then restart the daemon.
            """
        alert.alertStyle = .warning
        alert.addButton(withTitle: "OK")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
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
        let timer = Timer(timeInterval: stabilityWindow, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated { self?.restartCount = 0 }
        }
        stabilityTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    private func resolveDaemonPath() -> (URL, [String], String?) {
        // 1. Bundled single binary (production DMG)
        if let bundleResource = Bundle.main.resourceURL {
            let bundledBinary = bundleResource.appendingPathComponent("patcha")
            if FileManager.default.isExecutableFile(atPath: bundledBinary.path) {
                NSLog("[DaemonManager] using bundled binary: %@", bundledBinary.path)
                return (bundledBinary, ["daemon", "start"], nil)
            }
        }

        // 2. PATCHA_DAEMON_PATH env override
        if let envPath = ProcessInfo.processInfo.environment["PATCHA_DAEMON_PATH"] {
            let url = URL(fileURLWithPath: envPath)
            if FileManager.default.isExecutableFile(atPath: url.path) {
                NSLog("[DaemonManager] using PATCHA_DAEMON_PATH: %@", envPath)
                return (url, ["daemon", "start"], nil)
            }
        }

        // 3. Dev mode: look for Rust build output relative to project dir
        let projectDir = detectProjectDir()
        for relPath in ["rust/target/release/patcha", "rust/target/debug/patcha"] {
            let bin = "\(projectDir)/\(relPath)"
            if FileManager.default.isExecutableFile(atPath: bin) {
                NSLog("[DaemonManager] dev mode: %@", bin)
                return (URL(fileURLWithPath: bin), ["daemon", "start"], nil)
            }
        }

        NSLog("[DaemonManager] patcha binary not found — daemon cannot start")
        DaemonManager.showBinaryMissingAlert()
        status = .failed
        return (URL(fileURLWithPath: "/usr/bin/false"), [], nil)
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

    @MainActor private static func showBinaryMissingAlert() {
        let alert = NSAlert()
        alert.messageText = "patcha binary not found"
        alert.informativeText = """
            The patcha daemon binary could not be found.

            In development, build it with:
              cd rust && cargo build --release

            Then relaunch Patcha.
            """
        alert.alertStyle = .critical
        alert.addButton(withTitle: "OK")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    private func detectProjectDir() -> String {
        if let envDir = ProcessInfo.processInfo.environment["PATCHA_PROJECT_DIR"] {
            return envDir
        }

        // Embedded at build time via Info.plist PatchaSourceRoot = $(SRCROOT)/../..
        if let plistRoot = Bundle.main.infoDictionary?["PatchaSourceRoot"] as? String {
            let resolved = (plistRoot as NSString).standardizingPath
            if FileManager.default.fileExists(atPath: "\(resolved)/rust") {
                return resolved
            }
        }

        // In .app bundle: bundle lives at <project>/dist/Patcha.app — two levels up is project root.
        let bundlePath = Bundle.main.bundlePath
        let distDir = (bundlePath as NSString).deletingLastPathComponent
        let candidate = (distDir as NSString).deletingLastPathComponent
        if FileManager.default.fileExists(atPath: "\(candidate)/rust") {
            return candidate
        }

        // Walk up from the executable to find the rust/ directory.
        if let execPath = Bundle.main.executablePath {
            var dir = (execPath as NSString).deletingLastPathComponent
            for _ in 0..<10 {
                if FileManager.default.fileExists(atPath: "\(dir)/rust") {
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
