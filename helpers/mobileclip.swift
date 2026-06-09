// MobileCLIP image-encoder helper — persistent Core ML process.
//
// Loads the MobileCLIP image encoder (.mlpackage) once, then serves embedding
// requests over stdin/stdout so the Rust daemon doesn't pay the model-load cost
// per frame (mirrors the ax_content / ocr helper pattern, but long-lived).
//
// Protocol (line-delimited, UTF-8):
//   stdin:  one image file path per line
//   stdout: one JSON object per line — {"embedding":[...512]} or {"error":"..."}
//   A {"ready":true} line is emitted on stdout once the model is loaded.
//
// Usage: mobileclip <model.mlpackage>
//
// The model takes a 256x256 image and outputs a 512-d float vector
// (output feature "final_emb_1", shape [1, 512]). Vectors are returned raw
// (not L2-normalized); the caller computes cosine similarity.

import CoreML
import Foundation

func emit(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj),
          let line = String(data: data, encoding: .utf8) else { return }
    print(line)
    fflush(stdout)
}

func fail(_ msg: String) -> Never {
    emit(["error": msg])
    exit(1)
}

let args = Array(CommandLine.arguments.dropFirst())
guard let pkgArg = args.first else { fail("usage: mobileclip <model.mlpackage>") }
let pkgURL = URL(fileURLWithPath: pkgArg)

// Compile + load once. computeUnits=.all lets Core ML place the model on the ANE.
let compiledURL: URL
do { compiledURL = try MLModel.compileModel(at: pkgURL) }
catch { fail("compile failed: \(error.localizedDescription)") }

let cfg = MLModelConfiguration()
cfg.computeUnits = .all
let model: MLModel
do { model = try MLModel(contentsOf: compiledURL, configuration: cfg) }
catch { fail("load failed: \(error.localizedDescription)") }

// Resolve the image input + multiarray output names/constraints from the model.
var imageInputName: String?
var imageConstraint: MLImageConstraint?
for (name, desc) in model.modelDescription.inputDescriptionsByName
where desc.type == .image {
    imageInputName = name
    imageConstraint = desc.imageConstraint
}
var outputName: String?
for (name, desc) in model.modelDescription.outputDescriptionsByName
where desc.type == .multiArray {
    outputName = name
    break
}
guard let inName = imageInputName, let constraint = imageConstraint, let outName = outputName
else { fail("model lacks an image input or multiarray output") }

struct EmbedError: Error { let msg: String }

func embed(_ path: String) -> Result<[Float], EmbedError> {
    let url = URL(fileURLWithPath: path)
    guard let fv = try? MLFeatureValue(imageAt: url, constraint: constraint, options: nil) else {
        return .failure(EmbedError(msg: "cannot load image: \(path)"))
    }
    guard let provider = try? MLDictionaryFeatureProvider(dictionary: [inName: fv]) else {
        return .failure(EmbedError(msg: "cannot build feature provider"))
    }
    guard let out = try? model.prediction(from: provider),
          let arr = out.featureValue(for: outName)?.multiArrayValue else {
        return .failure(EmbedError(msg: "prediction failed"))
    }
    var v = [Float](repeating: 0, count: arr.count)
    let ptr = arr.dataPointer.assumingMemoryBound(to: Float32.self)
    if arr.dataType == .float32 {
        for i in 0..<arr.count { v[i] = ptr[i] }
    } else {
        for i in 0..<arr.count { v[i] = arr[i].floatValue }
    }
    return .success(v)
}

emit(["ready": true])

// Serve requests until stdin closes.
while let line = readLine(strippingNewline: true) {
    let path = line.trimmingCharacters(in: .whitespaces)
    if path.isEmpty { continue }
    switch embed(path) {
    case .success(let v): emit(["embedding": v])
    case .failure(let e): emit(["error": e.msg])
    }
}
