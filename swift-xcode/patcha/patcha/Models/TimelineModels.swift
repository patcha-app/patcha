import Foundation

struct TimelineDay: Decodable {
    let date: String
    let hours: [TimelineHour]
}

struct TimelineHour: Decodable, Identifiable {
    let hour: Int
    let apps: [String]
    let summaries: [String]
    let categories: [String]
    let eventCount: Int

    var id: Int { hour }

    enum CodingKeys: String, CodingKey {
        case hour, apps, summaries, categories
        case eventCount = "event_count"
    }
}
