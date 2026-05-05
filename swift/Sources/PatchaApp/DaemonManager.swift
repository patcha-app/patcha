import Foundation
import Combine

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
        let (execURL, args) = resolveDaemonPath()
        let proc = Process()
        proc.executableURL = execURL
        proc.arguments = args

        var env = ProcessInfo.processInfo.environment
        if let home = env["HOME"] {
            env["PATH"] = "\(home)/.local/bin:/usr/local/bin:/usr/bin:/bin"
        }
        proc.environment = env

        do {
            status = .starting
            try proc.run()
        } catch {
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

    private func resolveDaemonPath() -> (URL, [String]) {
        if let bundleResource = Bundle.main.resourceURL {
            let bundledBinary = bundleResource.appendingPathComponent("patcha")
            if FileManager.default.isExecutableFile(atPath: bundledBinary.path) {
                return (bundledBinary, [])
            }
        }

        if let envPath = ProcessInfo.processInfo.environment["PATCHA_DAEMON_PATH"] {
            let url = URL(fileURLWithPath: envPath)
            if FileManager.default.isExecutableFile(atPath: url.path) {
                return (url, [])
            }
        }

        let uvPath = findExecutable("uv") ?? "/usr/local/bin/uv"
        let projectDir = defaultProjectDir()
        return (
            URL(fileURLWithPath: uvPath),
            ["run", "--project", projectDir, "python", "\(projectDir)/main.py"]
        )
    }

    private func findExecutable(_ name: String) -> String? {
        let searchPaths = [
            (ProcessInfo.processInfo.environment["HOME"] ?? "") + "/.local/bin",
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/usr/bin",
        ]
        for dir in searchPaths {
            let path = "\(dir)/\(name)"
            if FileManager.default.isExecutableFile(atPath: path) {
                return path
            }
        }
        return nil
    }

    private func defaultProjectDir() -> String {
        let home = ProcessInfo.processInfo.environment["HOME"] ?? NSHomeDirectory()
        return "\(home)/patcha-app/patcha"
    }
}
