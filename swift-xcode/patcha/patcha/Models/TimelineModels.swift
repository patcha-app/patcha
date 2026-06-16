import Foundation

struct TimelineDay: Decodable {
    let date: String
    let hours: [TimelineHour]
}

struct TimelineTreeNode: Decodable, Identifiable {
    let label: String
    let children: [TimelineTreeNode]?

    var id: String { label }
}

struct TimelineHour: Decodable, Identifiable {
    let hour: Int
    let apps: [String]
    let summaries: [String]
    let categories: [String]
    let eventCount: Int
    let title: String?
    let tree: TimelineTreeNode?

    var id: Int { hour }

    enum CodingKeys: String, CodingKey {
        case hour, apps, summaries, categories, title, tree
        case eventCount = "event_count"
    }

    var displayTitle: String {
        title ?? categories.first?.capitalized ?? apps.first ?? "Activity"
    }

    var narrative: String {
        summaries.isEmpty
            ? "No summary captured for this hour."
            : summaries.joined(separator: " ")
    }

    // Until the backend emits a real tree, synthesize one from the hour's
    // categories (mid nodes) with the apps distributed as leaves.
    var derivedTree: TimelineTreeNode {
        if let tree { return tree }

        let cats = categories.isEmpty ? [displayTitle] : categories
        var children: [TimelineTreeNode] = []
        for (index, category) in cats.enumerated() {
            let leaves = apps.enumerated()
                .filter { cats.count <= 1 || $0.offset % cats.count == index }
                .map { TimelineTreeNode(label: $0.element, children: nil) }
            children.append(
                TimelineTreeNode(label: category.capitalized, children: leaves.isEmpty ? nil : leaves)
            )
        }

        return TimelineTreeNode(label: displayTitle, children: children.isEmpty ? nil : children)
    }
}
