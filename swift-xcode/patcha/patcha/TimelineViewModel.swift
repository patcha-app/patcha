import AppKit
import Combine
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class TimelineViewModel: ObservableObject {
    @Published var day: TimelineDay?
    @Published var selectedDate: Date = Calendar.current.startOfDay(for: Date())
    @Published var isLoading = false
    @Published var errorText: String?

    private let port: Int
    private let service = TimelineService()
    private var iconCache: [String: NSImage] = [:]
    private let fallbackIcon = NSWorkspace.shared.icon(for: .applicationBundle)

    init(port: Int) {
        self.port = port
        buildIconLookup()
    }

    var isToday: Bool {
        Calendar.current.isDateInToday(selectedDate)
    }

    var canGoForward: Bool { !isToday }

    var sessionCount: Int { day?.hours.count ?? 0 }

    var eventTotal: Int { (day?.hours ?? []).reduce(0) { $0 + $1.eventCount } }

    var appCount: Int {
        var names = Set<String>()
        for hour in day?.hours ?? [] { names.formUnion(hour.apps) }
        return names.count
    }

    func load() {
        isLoading = true
        errorText = nil
        let date = selectedDate
        Task {
            do {
                let result = try await service.fetchTimeline(port: port, date: date)
                guard date == self.selectedDate else { return }
                self.day = result
            } catch {
                guard date == self.selectedDate else { return }
                self.day = nil
                self.errorText = error.localizedDescription
            }
            if date == self.selectedDate {
                self.isLoading = false
            }
        }
    }

    func goPrev() {
        guard let prev = Calendar.current.date(byAdding: .day, value: -1, to: selectedDate) else { return }
        selectedDate = Calendar.current.startOfDay(for: prev)
    }

    func goNext() {
        guard canGoForward,
              let next = Calendar.current.date(byAdding: .day, value: 1, to: selectedDate) else { return }
        selectedDate = Calendar.current.startOfDay(for: next)
    }

    func goToday() {
        selectedDate = Calendar.current.startOfDay(for: Date())
    }

    func icon(for name: String) -> NSImage {
        iconCache[name] ?? fallbackIcon
    }

    // Brand hues for common apps, with a stable hashed fallback palette so the
    // rail dot/line color is consistent across reloads.
    private static let brandColors: [String: String] = [
        "mail": "2AA4FF", "notion": "C9C9CD", "codex": "5B8CFF", "arc": "FF5A4D",
        "figma": "A259FF", "terminal": "25C065", "iterm": "25C065", "slack": "E01E5A",
        "zoom": "2D8CFF", "spotify": "1DB954", "discord": "5865F2", "safari": "2AA4FF",
        "xcode": "2D8CFF", "chrome": "EA4335", "messages": "25C065",
    ]

    private static let palette = [
        "2AA4FF", "5B8CFF", "A259FF", "FF5A4D", "25C065", "E01E5A", "2D8CFF", "1DB954",
    ]

    func color(forApp name: String) -> Color {
        let key = name.lowercased()
        if let hex = Self.brandColors.first(where: { key.contains($0.key) })?.value {
            return Color(hex: hex)
        }
        let index = abs(name.hashValue) % Self.palette.count
        return Color(hex: Self.palette[index])
    }

    private func buildIconLookup() {
        var map: [String: NSImage] = [:]

        for app in NSWorkspace.shared.runningApplications {
            guard let name = app.localizedName, let icon = app.icon else { continue }
            map[name] = icon
        }

        let appsURL = URL(fileURLWithPath: "/Applications")
        if let contents = try? FileManager.default.contentsOfDirectory(
            at: appsURL, includingPropertiesForKeys: nil, options: .skipsHiddenFiles
        ) {
            for url in contents where url.pathExtension == "app" {
                let name = (Bundle(url: url)?.infoDictionary?["CFBundleDisplayName"] as? String)
                    ?? (Bundle(url: url)?.infoDictionary?["CFBundleName"] as? String)
                    ?? url.deletingPathExtension().lastPathComponent
                if map[name] == nil {
                    map[name] = NSWorkspace.shared.icon(forFile: url.path)
                }
            }
        }

        iconCache = map
    }
}
