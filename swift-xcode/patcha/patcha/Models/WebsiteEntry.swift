import Foundation

struct WebsiteEntry: Identifiable, Equatable {
    var id: String { domain }
    let domain: String
    var faviconData: Data?
    var isExcluded: Bool
}
