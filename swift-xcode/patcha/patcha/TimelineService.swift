import Foundation

enum TimelineServiceError: LocalizedError {
    case badResponse
    case server(String)

    var errorDescription: String? {
        switch self {
        case .badResponse:
            return "Couldn't reach the patcha server."
        case .server(let message):
            return message
        }
    }
}

struct TimelineService {
    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = .current
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    func fetchTimeline(port: Int, date: Date) async throws -> TimelineDay {
        let dateString = Self.dateFormatter.string(from: date)
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/timeline?date=\(dateString)") else {
            throw TimelineServiceError.badResponse
        }

        var request = URLRequest(url: url, timeoutInterval: 8)
        request.httpMethod = "GET"

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let http = response as? HTTPURLResponse else {
            throw TimelineServiceError.badResponse
        }

        if http.statusCode != 200 {
            if let payload = try? JSONDecoder().decode([String: String].self, from: data),
               let message = payload["error"] {
                throw TimelineServiceError.server(message)
            }
            throw TimelineServiceError.server("Server returned status \(http.statusCode).")
        }

        return try JSONDecoder().decode(TimelineDay.self, from: data)
    }
}
