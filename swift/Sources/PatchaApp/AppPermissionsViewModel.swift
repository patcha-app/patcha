import AppKit

struct AppEntry: Identifiable {
    let id: String
    let name: String
    let icon: NSImage?
    var isExcluded: Bool
}

@MainActor
final class AppPermissionsViewModel: ObservableObject {
    @Published var apps: [AppEntry] = []
    @Published var isScanning = false

    private let store: SettingsStore

    init(store: SettingsStore) {
        self.store = store
    }

    func scan() {
        isScanning = true
        let excluded = store.excludedBundleIDs
        Task.detached(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            let entries = self.buildAppList(excluded: excluded)
            await MainActor.run {
                self.apps = entries
                self.isScanning = false
            }
        }
    }

    private nonisolated func buildAppList(excluded: Set<String>) -> [AppEntry] {
        var seen = Set<String>()
        var result: [AppEntry] = []

        for app in NSWorkspace.shared.runningApplications {
            guard let bid = app.bundleIdentifier,
                  !bid.hasPrefix("com.apple."),
                  bid != "com.patcha.app",
                  !seen.contains(bid) else { continue }
            seen.insert(bid)
            result.append(AppEntry(
                id: bid,
                name: app.localizedName ?? bid,
                icon: app.icon,
                isExcluded: excluded.contains(bid)
            ))
        }

        let appsURL = URL(fileURLWithPath: "/Applications")
        if let contents = try? FileManager.default.contentsOfDirectory(
            at: appsURL, includingPropertiesForKeys: nil, options: .skipsHiddenFiles
        ) {
            for url in contents where url.pathExtension == "app" {
                guard let bundle = Bundle(url: url),
                      let bid = bundle.bundleIdentifier,
                      !bid.hasPrefix("com.apple."),
                      bid != "com.patcha.app",
                      !seen.contains(bid) else { continue }
                seen.insert(bid)
                let name = bundle.infoDictionary?["CFBundleDisplayName"] as? String
                    ?? bundle.infoDictionary?["CFBundleName"] as? String
                    ?? url.deletingPathExtension().lastPathComponent
                let icon = NSWorkspace.shared.icon(forFile: url.path)
                result.append(AppEntry(
                    id: bid,
                    name: name,
                    icon: icon,
                    isExcluded: excluded.contains(bid)
                ))
            }
        }

        return result.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    func toggle(_ app: AppEntry) {
        if store.excludedBundleIDs.contains(app.id) {
            store.excludedBundleIDs.remove(app.id)
        } else {
            store.excludedBundleIDs.insert(app.id)
        }
        let names = apps.filter { store.excludedBundleIDs.contains($0.id) }.map(\.name)
        store.excludedAppNames = names.joined(separator: ",")
        store.save {}
        if let idx = apps.firstIndex(where: { $0.id == app.id }) {
            apps[idx].isExcluded = store.excludedBundleIDs.contains(app.id)
        }
    }
}
