import Foundation

func nextMidnight() -> Date {
    var comps = Calendar.current.dateComponents([.year, .month, .day], from: Date())
    comps.day = (comps.day ?? 0) + 1
    comps.hour = 0; comps.minute = 0; comps.second = 0
    return Calendar.current.date(from: comps) ?? Date().addingTimeInterval(86400)
}
