import Foundation
import SQLite3

final class SettingsStore: ObservableObject {
    @Published var pollInterval: Int = 60
    @Published var enableGit: Bool = true
    @Published var enableBrowser: Bool = true
    @Published var enableTerminal: Bool = true
    @Published var enableWindow: Bool = true
    @Published var enableAccessibility: Bool = true

    private let dbPath: String

    init() {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let dir = support.appendingPathComponent("patcha", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        dbPath = dir.appendingPathComponent("settings.db").path
        load()
    }

    func load() {
        var db: OpaquePointer?
        guard sqlite3_open(dbPath, &db) == SQLITE_OK else { return }
        defer { sqlite3_close(db) }

        sqlite3_exec(db, "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)", nil, nil, nil)

        let rows = query(db: db, sql: "SELECT key, value FROM settings")
        for (key, value) in rows {
            switch key {
            case "poll_interval":       pollInterval = Int(value) ?? 60
            case "enable_git":          enableGit = value == "true"
            case "enable_browser":      enableBrowser = value == "true"
            case "enable_terminal":     enableTerminal = value == "true"
            case "enable_window":       enableWindow = value == "true"
            case "enable_accessibility": enableAccessibility = value == "true"
            default: break
            }
        }
    }

    func save(onComplete: @escaping () -> Void) {
        var db: OpaquePointer?
        guard sqlite3_open(dbPath, &db) == SQLITE_OK else { return }
        defer { sqlite3_close(db) }

        sqlite3_exec(db, "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)", nil, nil, nil)

        let entries: [(String, String)] = [
            ("poll_interval",        String(pollInterval)),
            ("enable_git",           enableGit ? "true" : "false"),
            ("enable_browser",       enableBrowser ? "true" : "false"),
            ("enable_terminal",      enableTerminal ? "true" : "false"),
            ("enable_window",        enableWindow ? "true" : "false"),
            ("enable_accessibility", enableAccessibility ? "true" : "false"),
        ]
        for (key, value) in entries {
            upsert(db: db, key: key, value: value)
        }

        onComplete()
    }

    private func query(db: OpaquePointer?, sql: String) -> [(String, String)] {
        var stmt: OpaquePointer?
        var results: [(String, String)] = []
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        while sqlite3_step(stmt) == SQLITE_ROW {
            let key = String(cString: sqlite3_column_text(stmt, 0))
            let val = String(cString: sqlite3_column_text(stmt, 1))
            results.append((key, val))
        }
        return results
    }

    private func upsert(db: OpaquePointer?, key: String, value: String) {
        var stmt: OpaquePointer?
        let sql = "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)"
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_text(stmt, 1, (key as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 2, (value as NSString).utf8String, -1, nil)
        sqlite3_step(stmt)
    }
}
