import AppKit
import Combine

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

    func addWebsite(_ rawInput: String) async {
        let domain = Self.parseDomain(rawInput)
        guard !domain.isEmpty, !store.websiteExclusions.contains(where: { $0.domain == domain }) else { return }
        let entry = WebsiteEntry(domain: domain, faviconData: nil, isExcluded: true)
        store.upsertWebsite(entry)
        guard let url = URL(string: "https://www.google.com/s2/favicons?domain=\(domain)&sz=64"),
              let (data, _) = try? await URLSession.shared.data(from: url) else { return }
        var updated = entry
        updated.faviconData = data
        store.upsertWebsite(updated)
    }

    func toggleWebsite(_ entry: WebsiteEntry) {
        var updated = entry
        updated.isExcluded = !entry.isExcluded
        store.upsertWebsite(updated)
    }

    func removeWebsite(_ domain: String) {
        store.removeWebsite(domain: domain)
    }

    private static func parseDomain(_ input: String) -> String {
        var s = input.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !s.hasPrefix("http") { s = "https://" + s }
        guard let host = URL(string: s)?.host else { return "" }
        return host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
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
