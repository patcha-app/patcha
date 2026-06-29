import AppKit
import Combine
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

    func icon(for name: String) -> NSImage {
        iconCache[name] ?? fallbackIcon
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
