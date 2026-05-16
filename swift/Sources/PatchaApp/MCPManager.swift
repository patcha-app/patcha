import AppKit
import Foundation

@MainActor class MCPManager: ObservableObject {
    @Published var isRunning = false

    private var process: Process?
    private var processSource: DispatchSourceProcess?
    var authToken: String?

    func start() {
        guard process == nil else { return }
        launchProcess()
    }

    func stop() {
        processSource?.cancel()
        processSource = nil

        guard let proc = process, proc.isRunning else {
            process = nil
            isRunning = false
            return
        }

        let pid = proc.processIdentifier
        kill(pid, SIGCONT)
        proc.terminate()
        process = nil
        isRunning = false

        Task.detached {
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            if kill(pid, 0) == 0 {
                kill(pid, SIGKILL)
            }
        }
    }

    private func launchProcess() {
        let (execURL, args, workDir) = resolveExecutable()

        guard FileManager.default.isExecutableFile(atPath: execURL.path) else {
            NSLog("[MCPManager] executable not found: %@", execURL.path)
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
            env["PATCHA_AUTH_TOKEN"] = token
        }
        proc.environment = env

        let errPipe = Pipe()
        proc.standardError = errPipe
        errPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if !data.isEmpty, let line = String(data: data, encoding: .utf8) {
                NSLog("[patcha-mcp] %@", line)
            }
        }

        do {
            try proc.run()
        } catch {
            NSLog("[MCPManager] launch failed: %@", error.localizedDescription)
            return
        }

        process = proc
        isRunning = true

        let pid = proc.processIdentifier
        let source = DispatchSource.makeProcessSource(identifier: pid, eventMask: .exit, queue: .main)
        source.setEventHandler { [weak self] in
            self?.handleProcessExit()
        }
        source.resume()
        processSource = source
    }

    private func handleProcessExit() {
        processSource?.cancel()
        processSource = nil
        process = nil
        isRunning = false
        NSLog("[MCPManager] MCP server exited")
    }

    private func resolveExecutable() -> (URL, [String], String?) {
        if let bundleResource = Bundle.main.resourceURL {
            let bundledBinary = bundleResource.appendingPathComponent("patcha-mcp")
            if FileManager.default.isExecutableFile(atPath: bundledBinary.path) {
                NSLog("[MCPManager] using bundled binary: %@", bundledBinary.path)
                return (bundledBinary, ["--http"], nil)
            }
        }

        if let envPath = ProcessInfo.processInfo.environment["PATCHA_MCP_PATH"] {
            let url = URL(fileURLWithPath: envPath)
            if FileManager.default.isExecutableFile(atPath: url.path) {
                NSLog("[MCPManager] using PATCHA_MCP_PATH: %@", envPath)
                return (url, ["--http"], nil)
            }
        }

        let projectDir = detectProjectDir()
        guard let uv = findExecutable("uv") else {
            NSLog("[MCPManager] uv not found — MCP server cannot start")
            return (URL(fileURLWithPath: "/usr/bin/false"), [], nil)
        }

        NSLog("[MCPManager] dev mode: uv=%@ project=%@", uv, projectDir)
        return (
            URL(fileURLWithPath: uv),
            ["run", "patcha-mcp", "--http"],
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

    private func detectProjectDir() -> String {
        if let envDir = ProcessInfo.processInfo.environment["PATCHA_PROJECT_DIR"] {
            return envDir
        }

        let bundlePath = Bundle.main.bundlePath
        let distDir = (bundlePath as NSString).deletingLastPathComponent
        let candidate = (distDir as NSString).deletingLastPathComponent
        if FileManager.default.fileExists(atPath: "\(candidate)/main.py") {
            return candidate
        }

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

        return (Bundle.main.bundlePath as NSString).deletingLastPathComponent
    }
}
