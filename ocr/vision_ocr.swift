// Offline OCR via the macOS Vision framework.
//   swift ocr/vision_ocr.swift <image.png> [...]
// Prints one JSON object per image: {"width":w,"height":h,"words":[{"text","x0","top","x1","bottom","conf"}]}
// Coordinates are in pixels with the origin at the top-left, matching PDF word boxes.
import Foundation
import Vision
import AppKit

func recognise(_ path: String) -> [String: Any]? {
    guard let image = NSImage(contentsOfFile: path),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        return nil
    }
    let width = CGFloat(cg.width), height = CGFloat(cg.height)
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-US"]

    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do { try handler.perform([request]) } catch { return nil }

    var words: [[String: Any]] = []
    for observation in (request.results ?? []) {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let text = candidate.string
        // Split the recognised line into words and locate each one separately,
        // so the parser gets the same word boxes a text PDF would give it.
        var searched = text.startIndex
        for token in text.split(separator: " ") {
            guard let range = text.range(of: String(token), range: searched..<text.endIndex) else { continue }
            searched = range.upperBound
            let box = (try? candidate.boundingBox(for: range))?.boundingBox ?? observation.boundingBox
            words.append([
                "text": String(token),
                "x0": Double(box.minX * width),
                "x1": Double(box.maxX * width),
                // Vision's origin is bottom-left; flip to top-left.
                "top": Double((1 - box.maxY) * height),
                "bottom": Double((1 - box.minY) * height),
                "conf": Double(candidate.confidence),
            ])
        }
    }
    return ["width": Double(width), "height": Double(height), "words": words]
}

var out: [[String: Any]] = []
for path in CommandLine.arguments.dropFirst() {
    out.append(recognise(path) ?? ["width": 0, "height": 0, "words": []])
}
let data = try JSONSerialization.data(withJSONObject: out, options: [])
FileHandle.standardOutput.write(data)
