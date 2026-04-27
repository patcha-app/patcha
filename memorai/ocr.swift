import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count > 1 else { exit(1) }
let imagePath = CommandLine.arguments[1]

guard
    let image = NSImage(contentsOfFile: imagePath),
    let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
else { exit(1) }

let semaphore = DispatchSemaphore(value: 0)
var recognized = ""

let request = VNRecognizeTextRequest { req, _ in
    defer { semaphore.signal() }
    guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
    recognized = obs.compactMap { $0.topCandidates(1).first?.string }.joined(separator: " ")
}
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try? handler.perform([request])
semaphore.wait()

let maxLen = 2000
print(recognized.count > maxLen ? String(recognized.prefix(maxLen)) : recognized)
