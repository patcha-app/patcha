import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count > 1 else { exit(1) }
let imagePath = CommandLine.arguments[1]

guard
    let image = NSImage(contentsOfFile: imagePath),
    let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
else { exit(1) }

struct TextObs: Codable {
    let text: String
    let x: Double
    let y: Double
    let w: Double
    let h: Double
}

let semaphore = DispatchSemaphore(value: 0)
var observations: [TextObs] = []

let request = VNRecognizeTextRequest { req, _ in
    defer { semaphore.signal() }
    guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
    for observation in obs {
        guard let text = observation.topCandidates(1).first?.string else { continue }
        let b = observation.boundingBox
        observations.append(TextObs(
            text: text,
            x: Double(b.origin.x),
            y: Double(b.origin.y),
            w: Double(b.size.width),
            h: Double(b.size.height)
        ))
    }
}
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try? handler.perform([request])
semaphore.wait()

if let data = try? JSONEncoder().encode(observations),
   let json = String(data: data, encoding: .utf8) {
    print(json)
}
