import AppKit
import ApplicationServices

// MARK: - AX helpers

@discardableResult
func cfGet(_ el: AXUIElement, _ attr: CFString, _ out: inout CFTypeRef?) -> Bool {
    AXUIElementCopyAttributeValue(el, attr, &out) == .success && out != nil
}

func axStr(_ el: AXUIElement, _ attr: CFString) -> String? {
    var val: CFTypeRef?
    guard cfGet(el, attr, &val) else { return nil }
    return val as? String
}

func axEl(_ el: AXUIElement, _ attr: CFString) -> AXUIElement? {
    var val: CFTypeRef?
    guard cfGet(el, attr, &val), let v = val else { return nil }
    return unsafeBitCast(v, to: AXUIElement.self)
}

func axEls(_ el: AXUIElement, _ attr: CFString) -> [AXUIElement] {
    var val: CFTypeRef?
    guard cfGet(el, attr, &val), let v = val, CFGetTypeID(v) == CFArrayGetTypeID() else { return [] }
    let arr = v as! CFArray
    return (0..<CFArrayGetCount(arr)).compactMap { i in
        guard let ptr = CFArrayGetValueAtIndex(arr, i) else { return nil }
        return Unmanaged<AXUIElement>.fromOpaque(ptr).takeUnretainedValue()
    }
}

func axFrame(_ el: AXUIElement) -> CGRect {
    var val: CFTypeRef?
    guard cfGet(el, "AXFrame" as CFString, &val), let v = val,
          CFGetTypeID(v) == AXValueGetTypeID() else { return .zero }
    var rect = CGRect.zero
    AXValueGetValue(v as! AXValue, .cgRect, &rect)
    return rect
}

func axFrameArea(_ el: AXUIElement) -> Double {
    let r = axFrame(el)
    return Double(r.width * r.height)
}

func extractText(_ el: AXUIElement, maxChars: Int = 4000) -> String? {
    let role = axStr(el, "AXRole" as CFString) ?? ""

    // Web areas and rich-text editors (Notion, Slack — Electron AXTextArea with DOM children):
    // use positioned subtree collection so table rows and inline elements stay on one line.
    if role == "AXWebArea" { return extractWebText(el, maxChars: maxChars) }
    if role == "AXTextArea" {
        let viewport = axFrame(el)
        let nodes = gatherTextNodes(el, viewport: viewport, minX: 0)
        if let t = spatialJoin(nodes, maxChars: maxChars) { return t }
        // No positioned children — fall through to string-range path below.
    }

    // Native scroll views (Terminal, TextEdit): AXVisibleCharacterRange is accurate.
    var rangeRef: CFTypeRef?
    if cfGet(el, "AXVisibleCharacterRange" as CFString, &rangeRef), let rv = rangeRef {
        var textRef: CFTypeRef?
        if AXUIElementCopyParameterizedAttributeValue(
            el, "AXStringForRange" as CFString, rv, &textRef) == .success,
           let t = textRef as? String {
            let trimmed = t.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.count >= 30 {
                return trimmed.count > maxChars ? String(trimmed.prefix(maxChars)) : trimmed
            }
        }
    }
    if let t = axStr(el, "AXValue" as CFString) {
        let trimmed = t.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.count >= 30 {
            return trimmed.count > maxChars ? String(trimmed.prefix(maxChars)) : trimmed
        }
    }
    return nil
}

// BFS to find the first element with a given AXSubrole (e.g. "AXLandmarkMain").
func findSubrole(_ root: AXUIElement, _ subrole: String) -> AXUIElement? {
    var queue: [AXUIElement] = [root]
    while !queue.isEmpty {
        let el = queue.removeFirst()
        if axStr(el, "AXSubrole" as CFString) == subrole { return el }
        axEls(el, "AXChildren" as CFString).forEach { queue.append($0) }
    }
    return nil
}

private struct TextNode {
    let text: String
    let frame: CGRect  // .zero means no position info
}

// Collect text nodes from a subtree, filtering by viewport and optional minX.
private func gatherTextNodes(_ root: AXUIElement, viewport: CGRect, minX: CGFloat) -> [TextNode] {
    let textRoles: Set<String> = ["AXStaticText", "AXHeading", "AXLink"]
    var nodes: [TextNode] = []
    var queue: [AXUIElement] = axEls(root, "AXChildren" as CFString)
    while !queue.isEmpty {
        let el = queue.removeFirst()
        let role = axStr(el, "AXRole" as CFString) ?? ""
        if textRoles.contains(role) {
            let f = axFrame(el)
            if viewport != .zero, f != .zero, !viewport.intersects(f) { continue }
            if minX > 0, f != .zero, f.midX < minX { continue }
            let val = axStr(el, "AXValue" as CFString) ?? axStr(el, "AXTitle" as CFString) ?? ""
            let trimmed = val.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { nodes.append(TextNode(text: trimmed, frame: f)) }
        } else {
            axEls(el, "AXChildren" as CFString).forEach { queue.append($0) }
        }
    }
    return nodes
}

// Reconstruct 2-D layout: nodes with similar Y are placed on the same line (sorted by X).
// This prevents table columns and side-by-side labels from stacking vertically.
private func spatialJoin(_ nodes: [TextNode], maxChars: Int) -> String? {
    guard !nodes.isEmpty else { return nil }

    // Estimate line-height as the median node height (clamped to 8–32px).
    let heights = nodes.compactMap { $0.frame == .zero ? nil : $0.frame.height }
    let lineThreshold: CGFloat
    if heights.isEmpty {
        lineThreshold = 12
    } else {
        let sorted = heights.sorted()
        let median = sorted[sorted.count / 2]
        lineThreshold = min(32, max(8, median * 0.55))
    }

    // Sort all nodes top-to-bottom, left-to-right within the same band.
    let positioned = nodes.filter { $0.frame != .zero }
                          .sorted { a, b in
                              if abs(a.frame.midY - b.frame.midY) < lineThreshold {
                                  return a.frame.midX < b.frame.midX
                              }
                              return a.frame.midY < b.frame.midY
                          }
    // Nodes with no frame go at the end.
    let noFrame = nodes.filter { $0.frame == .zero }

    // Group into lines.
    var lines: [[String]] = []
    var currentLine: [String] = []
    var lastMidY: CGFloat = -9999

    for node in positioned {
        if abs(node.frame.midY - lastMidY) > lineThreshold && !currentLine.isEmpty {
            lines.append(currentLine)
            currentLine = []
        }
        currentLine.append(node.text)
        lastMidY = node.frame.midY
    }
    if !currentLine.isEmpty { lines.append(currentLine) }
    for node in noFrame { lines.append([node.text]) }

    // Build final string, stopping at maxChars.
    var parts: [String] = []
    var total = 0
    for line in lines {
        let s = line.joined(separator: "  ")
        parts.append(s)
        total += s.count + 1
        if total >= maxChars { break }
    }

    let result = parts.joined(separator: "\n")
    return result.count >= 30 ? String(result.prefix(maxChars)) : nil
}

func collectTextNodes(_ root: AXUIElement, maxChars: Int = 4000,
                      viewport: CGRect = .zero, minX: CGFloat = 0) -> String? {
    let nodes = gatherTextNodes(root, viewport: viewport, minX: minX)
    return spatialJoin(nodes, maxChars: maxChars)
}

// Extract text from an AXWebArea.
// The web area's own frame is the visible viewport — content scrolled out of view is excluded.
func extractWebText(_ root: AXUIElement, maxChars: Int = 4000) -> String? {
    let viewport = axFrame(root)
    // 1. ARIA main landmark — maps to <main> in HTML; Slack, GitHub, Notion all use it.
    if let main = findSubrole(root, "AXLandmarkMain") {
        if let t = collectTextNodes(main, maxChars: maxChars, viewport: viewport) { return t }
    }
    // 2. Geometric fallback: viewport filter + skip leftmost 20% (navigation sidebars).
    let sidebarCutoff = viewport.minX + viewport.width * 0.20
    return collectTextNodes(root, maxChars: maxChars, viewport: viewport, minX: sidebarCutoff)
}

// Walk up the ancestor chain to find the nearest AXWebArea or AXTextArea container.
// minHeight skips single-line toolbar elements like the address bar (typically ~29px).
func nearestContentAncestor(_ el: AXUIElement, minHeight: CGFloat = 100) -> AXUIElement? {
    let contentRoles: Set<String> = ["AXWebArea", "AXTextArea"]
    var current: AXUIElement? = el
    while let c = current {
        if let role = axStr(c, "AXRole" as CFString), contentRoles.contains(role) {
            let frame = axFrame(c)
            if frame == .zero || frame.height >= minHeight {
                return c
            }
        }
        current = axEl(c, "AXParent" as CFString)
    }
    return nil
}

// BFS
func findContentEls(_ root: AXUIElement, maxDepth: Int = 15) -> [(el: AXUIElement, role: String)] {
    let contentRoles: Set<String> = ["AXWebArea", "AXTextArea"]
    var found: [(el: AXUIElement, role: String)] = []
    var queue: [(el: AXUIElement, depth: Int)] = [(root, 0)]
    while !queue.isEmpty {
        let (el, depth) = queue.removeFirst()
        let role = axStr(el, "AXRole" as CFString) ?? ""
        if contentRoles.contains(role) {
            found.append((el, role))
            continue
        }
        if depth < maxDepth {
            axEls(el, "AXChildren" as CFString).forEach { queue.append(($0, depth + 1)) }
        }
    }
    return found
}

// MARK: - Output

struct FrameRect: Encodable {
    let x: Double; let y: Double; let w: Double; let h: Double
}

struct Out: Encodable {
    let app: String
    let window_title: String
    let content: String
    let source: String  // "ax_focused" | "ax_web" | "ax_text" | "ocr_needed"
    let frame: FrameRect?
}

func emit(_ out: Out) {
    if let data = try? JSONEncoder().encode(out),
       let s = String(data: data, encoding: .utf8) { print(s) }
}

// MARK: - Diag helpers

func findAllLandmarks(_ root: AXUIElement) -> [(subrole: String, frame: CGRect)] {
    let landmarkPrefixes = ["AXLandmark", "AXApplication", "AXBanner"]
    var found: [(String, CGRect)] = []
    var queue: [AXUIElement] = [root]
    while !queue.isEmpty {
        let el = queue.removeFirst()
        if let sub = axStr(el, "AXSubrole" as CFString),
           landmarkPrefixes.contains(where: { sub.hasPrefix($0) }) {
            found.append((sub, axFrame(el)))
        }
        axEls(el, "AXChildren" as CFString).forEach { queue.append($0) }
    }
    return found
}

func preview(_ text: String?, limit: Int = 120) -> String {
    guard let t = text else { return "<nil>" }
    let flat = t.components(separatedBy: .newlines)
                 .map { $0.trimmingCharacters(in: .whitespaces) }
                 .filter { !$0.isEmpty }
                 .joined(separator: " ↵ ")
    return flat.count > limit ? String(flat.prefix(limit)) + "…" : flat
}

func frameDesc(_ rect: CGRect) -> String {
    guard rect != .zero else { return "no frame" }
    return String(format: "x=%.0f y=%.0f w=%.0f h=%.0f area=%.0f",
                  rect.origin.x, rect.origin.y, rect.width, rect.height,
                  rect.width * rect.height)
}

struct DiagResult: Encodable {
    struct StrategyResult: Encodable {
        let strategy: Int
        let name: String
        let fired: Bool
        let detail: String
        let element_role: String?
        let element_frame: String?
        let content_chars: Int?
        let content_preview: String?
    }

    struct BFSCandidate: Encodable {
        let rank: Int
        let role: String
        let frame: String
        let area: Double
        let title: String?
        let content_chars: Int?
        let content_preview: String?
        let selected: Bool
    }

    struct WebAreaAnalysis: Encodable {
        let web_area_frame: String
        let landmark_main_found: Bool
        let landmark_main_frame: String?
        let sidebar_cutoff_x: Double
        let all_landmarks: [String]
    }

    let ax_trusted: Bool
    let app: String
    let window_title: String
    let cursor_pos: String
    let strategies: [StrategyResult]
    let bfs_candidates: [BFSCandidate]
    let web_area_analysis: WebAreaAnalysis?
    let winner: String
    let full_content: String?
}

// MARK: - Main

let diagMode = CommandLine.arguments.contains("--diag")

guard let frontApp = NSWorkspace.shared.frontmostApplication else {
    if diagMode {
        fputs("ERROR: no frontmost application\n", stderr)
        exit(1)
    }
    emit(Out(app: "", window_title: "", content: "", source: "ocr_needed", frame: nil))
    exit(0)
}

let pid = frontApp.processIdentifier
let appName = frontApp.localizedName ?? ""
let appEl = AXUIElementCreateApplication(pid)

let frontWin = axEl(appEl, "AXFocusedWindow" as CFString)
    ?? axEls(appEl, "AXWindows" as CFString).first
let windowTitle = frontWin.flatMap { axStr($0, "AXTitle" as CFString) } ?? ""
let windowRoot = frontWin ?? appEl

if diagMode {
    let axTrusted = AXIsProcessTrusted()
    if !axTrusted {
        fputs("WARNING: Accessibility API not trusted — grant permission in System Settings → Privacy & Security → Accessibility.\n", stderr)
    }

    var strategies: [DiagResult.StrategyResult] = []
    var winner = "ocr_needed"
    var winnerContent: String? = nil

    let focusedEl = axEl(appEl, "AXFocusedUIElement" as CFString)
    let focusedRole = focusedEl.flatMap { axStr($0, "AXRole" as CFString) }
    let focusedContainer = focusedEl.flatMap { nearestContentAncestor($0) }
    let focusedText = focusedContainer.flatMap { extractText($0) }

    if focusedEl == nil {
        strategies.append(.init(
            strategy: 1, name: "keyboard_focus_walkup", fired: false,
            detail: "no AXFocusedUIElement on app",
            element_role: nil, element_frame: nil, content_chars: nil, content_preview: nil))
    } else if focusedContainer == nil {
        strategies.append(.init(
            strategy: 1, name: "keyboard_focus_walkup", fired: false,
            detail: "focused element role=\(focusedRole ?? "?") — no AXWebArea/AXTextArea ancestor",
            element_role: focusedRole, element_frame: nil, content_chars: nil, content_preview: nil))
    } else if focusedText == nil {
        let frame = focusedContainer.map { axFrame($0) } ?? .zero
        strategies.append(.init(
            strategy: 1, name: "keyboard_focus_walkup", fired: false,
            detail: "container found but extractText returned nil (content < 30 chars?)",
            element_role: focusedContainer.flatMap { axStr($0, "AXRole" as CFString) },
            element_frame: frameDesc(frame),
            content_chars: 0, content_preview: nil))
    } else {
        let frame = focusedContainer.map { axFrame($0) } ?? .zero
        let role = focusedContainer.flatMap { axStr($0, "AXRole" as CFString) } ?? ""
        strategies.append(.init(
            strategy: 1, name: "keyboard_focus_walkup", fired: true,
            detail: "focused role=\(focusedRole ?? "?") → container role=\(role)",
            element_role: role, element_frame: frameDesc(frame),
            content_chars: focusedText!.count,
            content_preview: preview(focusedText)))
        winner = "strategy_1"
        winnerContent = focusedText
    }

    let mousePos = NSEvent.mouseLocation
    let screenHeight = Double(NSScreen.main?.frame.height ?? 900)
    let axY = Float(screenHeight - mousePos.y)
    let cursorDesc = String(format: "screen=(%.0f,%.0f) ax=(%.0f,%.0f)",
                            mousePos.x, mousePos.y, Double(mousePos.x), Double(axY))

    if winner == "ocr_needed" {
        var cursorEl: AXUIElement?
        let sysEl = AXUIElementCreateSystemWide()
        let cursorStatus = AXUIElementCopyElementAtPosition(sysEl, Float(mousePos.x), axY, &cursorEl)

        if cursorStatus != .success {
            strategies.append(.init(
                strategy: 2, name: "cursor_position", fired: false,
                detail: "AXUIElementCopyElementAtPosition failed (status \(cursorStatus.rawValue))",
                element_role: nil, element_frame: nil, content_chars: nil, content_preview: nil))
        } else if cursorEl == nil {
            strategies.append(.init(
                strategy: 2, name: "cursor_position", fired: false,
                detail: "no element at cursor \(cursorDesc)",
                element_role: nil, element_frame: nil, content_chars: nil, content_preview: nil))
        } else {
            let cursorRole = axStr(cursorEl!, "AXRole" as CFString)
            let cursorContainer = nearestContentAncestor(cursorEl!)
            let cursorText = cursorContainer.flatMap { extractText($0) }

            if cursorContainer == nil {
                strategies.append(.init(
                    strategy: 2, name: "cursor_position", fired: false,
                    detail: "element at cursor role=\(cursorRole ?? "?") — no AXWebArea/AXTextArea ancestor",
                    element_role: cursorRole, element_frame: nil, content_chars: nil, content_preview: nil))
            } else if cursorText == nil {
                let frame = cursorContainer.map { axFrame($0) } ?? .zero
                strategies.append(.init(
                    strategy: 2, name: "cursor_position", fired: false,
                    detail: "container found but extractText returned nil",
                    element_role: cursorContainer.flatMap { axStr($0, "AXRole" as CFString) },
                    element_frame: frameDesc(frame),
                    content_chars: 0, content_preview: nil))
            } else {
                let frame = cursorContainer.map { axFrame($0) } ?? .zero
                let role = cursorContainer.flatMap { axStr($0, "AXRole" as CFString) } ?? ""
                strategies.append(.init(
                    strategy: 2, name: "cursor_position", fired: true,
                    detail: "cursor element role=\(cursorRole ?? "?") → container role=\(role)",
                    element_role: role, element_frame: frameDesc(frame),
                    content_chars: cursorText!.count,
                    content_preview: preview(cursorText)))
                winner = "strategy_2"
                winnerContent = cursorText
            }
        }
    } else {
        strategies.append(.init(
            strategy: 2, name: "cursor_position", fired: false,
            detail: "skipped — strategy 1 already fired",
            element_role: nil, element_frame: nil, content_chars: nil, content_preview: nil))
    }

    let bfsEls = findContentEls(windowRoot)
    let bestEl = bfsEls.max(by: { axFrameArea($0.el) < axFrameArea($1.el) })
    let sortedCandidates = bfsEls.sorted { axFrameArea($0.el) > axFrameArea($1.el) }
    let bfsCandidates: [DiagResult.BFSCandidate] = sortedCandidates.enumerated().map { (i, c) in
        let frame = axFrame(c.el)
        let text = extractText(c.el)
        let isSelected = (winner == "ocr_needed") && (bestEl.map { axFrameArea($0.el) == axFrameArea(c.el) } ?? false)
        return DiagResult.BFSCandidate(
            rank: i + 1,
            role: c.role,
            frame: frameDesc(frame),
            area: axFrameArea(c.el),
            title: axStr(c.el, "AXTitle" as CFString),
            content_chars: text.map { $0.count },
            content_preview: preview(text, limit: 100),
            selected: isSelected
        )
    }

    if winner == "ocr_needed" {
        if let best = bestEl, let text = extractText(best.el) {
            let frame = axFrame(best.el)
            strategies.append(.init(
                strategy: 3, name: "bfs_largest_area", fired: true,
                detail: "\(bfsEls.count) candidate(s) found, selected largest by area",
                element_role: best.role, element_frame: frameDesc(frame),
                content_chars: text.count, content_preview: preview(text)))
            winner = "strategy_3"
            winnerContent = text
        } else {
            strategies.append(.init(
                strategy: 3, name: "bfs_largest_area", fired: false,
                detail: bfsEls.isEmpty
                    ? "BFS found 0 AXWebArea/AXTextArea elements in window tree"
                    : "\(bfsEls.count) element(s) found but none yielded extractable text",
                element_role: nil, element_frame: nil, content_chars: nil, content_preview: nil))
        }
    } else {
        strategies.append(.init(
            strategy: 3, name: "bfs_largest_area", fired: false,
            detail: "skipped — earlier strategy already fired (\(bfsCandidates.count) BFS candidate(s) available)",
            element_role: nil, element_frame: nil, content_chars: nil, content_preview: nil))
    }

    var webAnalysis: DiagResult.WebAreaAnalysis? = nil
    if let webCand = bfsEls.first(where: { $0.role == "AXWebArea" }) {
        let waFrame = axFrame(webCand.el)
        let mainEl = findSubrole(webCand.el, "AXLandmarkMain")
        let mainFrame = mainEl.map { axFrame($0) }
        let cutoff = waFrame.minX + waFrame.width * 0.20
        let landmarks = findAllLandmarks(webCand.el).map {
            "\($0.subrole) @ \(frameDesc($0.frame))"
        }
        webAnalysis = DiagResult.WebAreaAnalysis(
            web_area_frame: frameDesc(waFrame),
            landmark_main_found: mainEl != nil,
            landmark_main_frame: mainFrame.map { frameDesc($0) },
            sidebar_cutoff_x: Double(cutoff),
            all_landmarks: landmarks
        )
    }

    let diagEncoder = JSONEncoder()
    diagEncoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    let result = DiagResult(
        ax_trusted: axTrusted,
        app: appName,
        window_title: windowTitle,
        cursor_pos: cursorDesc,
        strategies: strategies,
        bfs_candidates: bfsCandidates,
        web_area_analysis: webAnalysis,
        winner: winner,
        full_content: winnerContent
    )
    if let data = try? diagEncoder.encode(result),
       let s = String(data: data, encoding: .utf8) { print(s) }
    exit(0)
}

func toFrameRect(_ r: CGRect) -> FrameRect? {
    guard r != .zero else { return nil }
    return FrameRect(x: Double(r.minX), y: Double(r.minY), w: Double(r.width), h: Double(r.height))
}

func nearestFrame(_ el: AXUIElement) -> CGRect {
    var current: AXUIElement? = el
    while let c = current {
        let r = axFrame(c)
        if r != .zero { return r }
        current = axEl(c, "AXParent" as CFString)
    }
    return .zero
}

// Strategy 1: keyboard focus — walk up from the focused element to the nearest content container
if let focused = axEl(appEl, "AXFocusedUIElement" as CFString),
   let container = nearestContentAncestor(focused),
   let text = extractText(container) {
    emit(Out(app: appName, window_title: windowTitle, content: text, source: "ax_focused", frame: toFrameRect(axFrame(container))))
    exit(0)
}

// Strategy 2: element under the mouse cursor — tells us exactly which pane the user is in
let mousePos = NSEvent.mouseLocation  // bottom-left origin (Cocoa)
let screenHeight = Double(NSScreen.main?.frame.height ?? 900)
let axY = Float(screenHeight - mousePos.y)  // flip to top-left origin for AX
var cursorEl: AXUIElement?
let sysEl = AXUIElementCreateSystemWide()
if AXUIElementCopyElementAtPosition(sysEl, Float(mousePos.x), axY, &cursorEl) == .success,
   let el = cursorEl,
   let container = nearestContentAncestor(el),
   let text = extractText(container) {
    let role = axStr(container, "AXRole" as CFString) ?? ""
    let src = role == "AXWebArea" ? "ax_web" : "ax_text"
    emit(Out(app: appName, window_title: windowTitle, content: text, source: src, frame: toFrameRect(axFrame(container))))
    exit(0)
}

// Strategy 3: largest content element in the frontmost window (fallback)
let candidates = findContentEls(windowRoot)
if let best = candidates.max(by: { axFrameArea($0.el) < axFrameArea($1.el) }),
   let text = extractText(best.el) {
    let src = best.role == "AXWebArea" ? "ax_web" : "ax_text"
    emit(Out(app: appName, window_title: windowTitle, content: text, source: src, frame: toFrameRect(axFrame(best.el))))
    exit(0)
}

// ocr_needed: no text extractable — emit best-effort frame so OCR can crop to the right region
let ocrFrame: FrameRect?
if let focused = axEl(appEl, "AXFocusedUIElement" as CFString) {
    ocrFrame = toFrameRect(nearestFrame(focused))
} else if AXUIElementCopyElementAtPosition(sysEl, Float(mousePos.x), axY, &cursorEl) == .success,
          let el = cursorEl {
    ocrFrame = toFrameRect(nearestFrame(el))
} else {
    ocrFrame = toFrameRect(axFrame(windowRoot))
}
emit(Out(app: appName, window_title: windowTitle, content: "", source: "ocr_needed", frame: ocrFrame))
