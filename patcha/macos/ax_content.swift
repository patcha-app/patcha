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

// Compute a horizontal column band [minX, maxX] within a container, anchored on the focus point.
// This isolates the pane the user is in (e.g. Slack's message list) from sidebars on the left
// and thread/detail panels on the right.
//   focusX:     screen X of the focused element or cursor (0 = unknown).
//   focusWidth: width of the focused element — "useful" only when < 70% of the container width
//               (a full-width frame is the container itself and gives no column information).
// Returns (0, .infinity) when there is no usable focus hint and the viewport is narrow.
func columnBounds(viewport: CGRect, focusX: CGFloat, focusWidth: CGFloat) -> (CGFloat, CGFloat) {
    let usefulWidth = focusWidth > 0 && focusWidth < viewport.width * 0.70
    if focusX > 0 && viewport.width > 600 {
        if usefulWidth {
            // Exact column from focused element frame + 80px left pad for sender avatars.
            return (max(viewport.minX, focusX - focusWidth / 2 - 80),
                    min(viewport.maxX, focusX + focusWidth / 2))
        }
        // Cursor-only or full-width focus: ±15% band around the point.
        let half = viewport.width * 0.15
        return (max(viewport.minX, focusX - half), min(viewport.maxX, focusX + half))
    }
    // No usable focus hint — skip leftmost 20% for wide viewports (sidebar heuristic).
    return (viewport.width > 800 ? viewport.minX + viewport.width * 0.20 : 0, .infinity)
}

func extractText(_ el: AXUIElement, maxChars: Int = 4000,
                 focusX: CGFloat = 0, focusWidth: CGFloat = 0) -> String? {
    let role = axStr(el, "AXRole" as CFString) ?? ""

    // Web areas and rich-text editors (Notion, Slack — Electron apps expose either AXWebArea
    // or AXTextArea with positioned DOM children): collect text nodes spatially so table rows
    // and inline elements stay on one line, and apply a column band around the focus point.
    if role == "AXWebArea" {
        return extractWebText(el, maxChars: maxChars, focusX: focusX, focusWidth: focusWidth)
    }
    if role == "AXTextArea" {
        let viewport = axFrame(el)
        let (minX, maxX) = columnBounds(viewport: viewport, focusX: focusX, focusWidth: focusWidth)

        // Try the ARIA main landmark with column bounds applied. AXLandmarkMain maps to <main>
        // but often wraps the full layout (sidebar + messages + thread panel), so the column
        // filter is still needed.
        if let main = findSubrole(el, "AXLandmarkMain") {
            let nodes = gatherTextNodes(main, viewport: viewport, minX: minX, maxX: maxX)
            if let t = spatialJoin(nodes, maxChars: maxChars) { return t }
        }

        // Direct column-filtered traversal over the full AXTextArea.
        let nodes = gatherTextNodes(el, viewport: viewport, minX: minX, maxX: maxX)
        if let t = spatialJoin(nodes, maxChars: maxChars) { return t }

        // Column filter produced nothing — return nil so the caller can try the next strategy.
        // Do NOT fall through to AXVisibleCharacterRange/AXValue, which would return the full
        // unfiltered window text and defeat the column filtering entirely.
        if minX > 0 || maxX < .infinity { return nil }
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

// Collect text nodes from a subtree, filtering by viewport, optional minX, and optional maxX.
private func gatherTextNodes(_ root: AXUIElement, viewport: CGRect,
                              minX: CGFloat = 0, maxX: CGFloat = .infinity) -> [TextNode] {
    let textRoles: Set<String> = ["AXStaticText", "AXHeading", "AXLink"]
    var nodes: [TextNode] = []
    var queue: [AXUIElement] = axEls(root, "AXChildren" as CFString)
    while !queue.isEmpty {
        let el = queue.removeFirst()
        let role = axStr(el, "AXRole" as CFString) ?? ""
        if textRoles.contains(role) {
            let f = axFrame(el)
            if viewport != .zero, f != .zero, !viewport.intersects(f) { continue }
            // When column bounds are active, drop frameless nodes — they are UI chrome
            // (icon labels, tooltips) that have no position and can't be placed in the layout.
            if (minX > 0 || maxX < .infinity), f == .zero { continue }
            if minX > 0, f != .zero, f.midX < minX { continue }
            if maxX < .infinity, f != .zero, f.midX > maxX { continue }
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

    // X-gap threshold: if two same-Y nodes are more than 30% of the total X span apart,
    // treat them as separate items (e.g. sender name vs. message body in Slack).
    let xMin = positioned.map { $0.frame.minX }.min() ?? 0
    let xMax = positioned.map { $0.frame.maxX }.max() ?? (xMin + 1)
    let xGapThreshold: CGFloat = max(100, (xMax - xMin) * 0.30)

    // Group into lines.
    var lines: [[String]] = []
    var currentLine: [String] = []
    var lastMidY: CGFloat = -9999
    var lastMaxX: CGFloat = -9999

    for node in positioned {
        let yDiff = abs(node.frame.midY - lastMidY)
        let xGap = currentLine.isEmpty ? 0 : (node.frame.minX - lastMaxX)
        let newLine = (!currentLine.isEmpty && yDiff > lineThreshold) ||
                      (!currentLine.isEmpty && yDiff <= lineThreshold && xGap > xGapThreshold)
        if newLine {
            lines.append(currentLine)
            currentLine = []
            // Insert a blank line between message blocks (large vertical jumps).
            if yDiff > lineThreshold * 5 && !lines.isEmpty {
                lines.append([])
            }
        }
        currentLine.append(node.text)
        lastMidY = node.frame.midY
        lastMaxX = node.frame.maxX
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
                      viewport: CGRect = .zero, minX: CGFloat = 0, maxX: CGFloat = .infinity) -> String? {
    let nodes = gatherTextNodes(root, viewport: viewport, minX: minX, maxX: maxX)
    return spatialJoin(nodes, maxChars: maxChars)
}

// Extract text from an AXWebArea.
// The web area's own frame is the visible viewport — content scrolled out of view is excluded.
// focusX/focusWidth anchor a column band (see columnBounds) so wide single-page apps like Slack
// don't mix the sidebar and thread/detail panel into the main content.
func extractWebText(_ root: AXUIElement, maxChars: Int = 4000,
                    focusX: CGFloat = 0, focusWidth: CGFloat = 0) -> String? {
    let viewport = axFrame(root)
    let (minX, maxX) = columnBounds(viewport: viewport, focusX: focusX, focusWidth: focusWidth)

    // 1. ARIA main landmark — maps to <main> in HTML; Slack, GitHub, Notion all use it.
    //    Column bounds are still applied because <main> can span the full window width.
    if let main = findSubrole(root, "AXLandmarkMain") {
        if let t = collectTextNodes(main, maxChars: maxChars, viewport: viewport,
                                    minX: minX, maxX: maxX) { return t }
    }
    // 2. Geometric fallback over the whole web area with the same column bounds.
    return collectTextNodes(root, maxChars: maxChars, viewport: viewport, minX: minX, maxX: maxX)
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
    let ocr_frame: String?
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

// --frame-only: skip text extraction entirely; emit app/window metadata plus a cursor-centered
// crop frame for the OCR pipeline. (Cursor position needs native code, hence this Swift helper.)
if CommandLine.arguments.contains("--frame-only") {
    let mousePos = NSEvent.mouseLocation
    let screenH  = Double(NSScreen.main?.frame.height ?? 900)
    let axY      = CGFloat(screenH - mousePos.y)  // flip Cocoa bottom-left → AX top-left
    let win = axFrame(windowRoot)
    // Prefer the AX pane (column) under the cursor; fall back to a tight cursor-centered box.
    var crop = paneFrameUnderCursor(cursorX: Float(mousePos.x), cursorAXY: Float(axY), window: win)
    if crop == .zero {
        crop = cursorCenteredCrop(window: win, cursorX: CGFloat(mousePos.x), cursorAXY: axY)
    }
    emit(Out(app: appName, window_title: windowTitle, content: "",
             source: "ocr", frame: toFrameRect(crop)))
    exit(0)
}

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

    // Compute what OCR frame would be used (same cascade as the non-diag path).
    var diagOcrFrame: CGRect = .zero
    if let f = focusedEl { diagOcrFrame = nearestFrame(f) }
    if diagOcrFrame == .zero {
        var diagCursorEl: AXUIElement?
        let diagSysEl = AXUIElementCreateSystemWide()
        if AXUIElementCopyElementAtPosition(diagSysEl, Float(mousePos.x), axY, &diagCursorEl) == .success,
           let el = diagCursorEl {
            diagOcrFrame = nearestFrame(el)
        }
    }
    if diagOcrFrame == .zero { diagOcrFrame = axFrame(windowRoot) }
    diagOcrFrame = narrowToViewport(diagOcrFrame, cursorX: CGFloat(mousePos.x), cursorAXY: CGFloat(axY))
    let diagOcrFrameDesc = diagOcrFrame == .zero ? "no frame (nil)" : frameDesc(diagOcrFrame)

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
        ocr_frame: diagOcrFrameDesc,
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

// When an app (e.g. GPU-rendered editors with minimal AX) only exposes the full window as
// the focused region, shrink to a cursor-centered viewport so OCR isn't run on the entire screen.
// Only kicks in when the frame occupies >= 85% of the primary screen area.
func narrowToViewport(_ frame: CGRect, cursorX: CGFloat, cursorAXY: CGFloat) -> CGRect {
    guard let screenFrame = NSScreen.main?.frame else { return frame }
    let screenArea = Double(screenFrame.width * screenFrame.height)
    let frameArea = Double(frame.width * frame.height)
    guard frameArea >= screenArea * 0.85 else { return frame }
    let vpW = min(frame.width, screenFrame.width * 0.55)
    let vpH = min(frame.height, screenFrame.height * 0.65)
    let x = max(frame.minX, min(frame.maxX - vpW, cursorX - vpW / 2))
    let y = max(frame.minY, min(frame.maxY - vpH, cursorAXY - vpH / 2))
    return CGRect(x: x, y: y, width: vpW, height: vpH)
}

// Cursor-centered crop fallback for the --frame-only OCR path: a viewport-sized region centered
// on the cursor, clamped to the window (or screen if the window frame is unavailable). Used only
// when AX pane detection yields nothing. Tight width so it tends to isolate one column.
func cursorCenteredCrop(window: CGRect, cursorX: CGFloat, cursorAXY: CGFloat) -> CGRect {
    let screen = NSScreen.main?.frame ?? CGRect(x: 0, y: 0, width: 1440, height: 900)
    let bounds = window == .zero ? screen : window
    let vpW = min(bounds.width,  screen.width  * 0.36)   // tunable; ~one Slack column
    let vpH = min(bounds.height, screen.height * 0.65)
    let x = max(bounds.minX, min(bounds.maxX - vpW, cursorX - vpW / 2))
    let y = max(bounds.minY, min(bounds.maxY - vpH, cursorAXY - vpH / 2))
    return CGRect(x: x, y: y, width: vpW, height: vpH)
}

// Find the on-screen PANE (column) under the cursor using AX geometry only — not text.
// Walk up from the element under the cursor, keeping the largest ancestor frame that is still
// narrower than `maxFrac` of the window. That isolates a single column (Slack sidebar / message
// list / thread panel) rather than the whole window (one big AXWebArea) or a tiny line element.
// Returns .zero when no usable pane frame is found (caller falls back to cursorCenteredCrop).
func paneFrameUnderCursor(cursorX: Float, cursorAXY: Float, window: CGRect,
                          maxFrac: CGFloat = 0.50, minFrac: CGFloat = 0.12) -> CGRect {
    let sysEl = AXUIElementCreateSystemWide()
    var hit: AXUIElement?
    guard AXUIElementCopyElementAtPosition(sysEl, cursorX, cursorAXY, &hit) == .success,
          let start = hit else { return .zero }

    let winW = window == .zero ? (NSScreen.main?.frame.width ?? 1440) : window.width
    var best: CGRect = .zero
    var current: AXUIElement? = start
    var hops = 0
    while let c = current, hops < 25 {
        let f = axFrame(c)
        if f != .zero {
            if f.width > winW * maxFrac { break }       // reached a full-width container; stop
            if f.width >= winW * minFrac { best = f }    // a plausible pane/column
        }
        current = axEl(c, "AXParent" as CFString)
        hops += 1
    }
    return best
}

// Read cursor position once — used by both Strategy 1 (fallback) and Strategy 2.
let mousePos = NSEvent.mouseLocation  // bottom-left origin (Cocoa)
let screenHeight = Double(NSScreen.main?.frame.height ?? 900)
let axY = Float(screenHeight - mousePos.y)  // flip to top-left origin for AX
let cursorX = CGFloat(mousePos.x)

// Strategy 1: keyboard focus — walk up from the focused element to the nearest content container.
// If the focused element has no usable frame (common in Electron apps like Slack), fall back to
// the cursor position so the column band is still anchored correctly.
if let focused = axEl(appEl, "AXFocusedUIElement" as CFString),
   let container = nearestContentAncestor(focused) {
    let focusedFrame = axFrame(focused)
    let containerFrame = axFrame(container)
    let frameIsUseful = focusedFrame != .zero && containerFrame != .zero
                        && focusedFrame.width < containerFrame.width * 0.70
    let effectiveFocusX    = frameIsUseful ? focusedFrame.midX  : cursorX
    let effectiveFocusWidth = frameIsUseful ? focusedFrame.width : CGFloat(0)
    if let text = extractText(container, focusX: effectiveFocusX, focusWidth: effectiveFocusWidth) {
        emit(Out(app: appName, window_title: windowTitle, content: text, source: "ax_focused", frame: toFrameRect(containerFrame)))
        exit(0)
    }
}

// Strategy 2: element under the mouse cursor — tells us exactly which pane the user is in
var cursorEl: AXUIElement?
let sysEl = AXUIElementCreateSystemWide()
if AXUIElementCopyElementAtPosition(sysEl, Float(mousePos.x), axY, &cursorEl) == .success,
   let el = cursorEl,
   let container = nearestContentAncestor(el),
   let text = extractText(container, focusX: cursorX) {
    let role = axStr(container, "AXRole" as CFString) ?? ""
    let src = role == "AXWebArea" ? "ax_web" : "ax_text"
    emit(Out(app: appName, window_title: windowTitle, content: text, source: src, frame: toFrameRect(axFrame(container))))
    exit(0)
}

// Strategy 3: largest content element in the frontmost window (fallback — no focus hint)
let candidates = findContentEls(windowRoot)
if let best = candidates.max(by: { axFrameArea($0.el) < axFrameArea($1.el) }),
   let text = extractText(best.el) {
    let src = best.role == "AXWebArea" ? "ax_web" : "ax_text"
    emit(Out(app: appName, window_title: windowTitle, content: text, source: src, frame: toFrameRect(axFrame(best.el))))
    exit(0)
}

// ocr_needed: no text extractable — emit best-effort frame so OCR can crop to the right region
// Priority: keyboard focus > mouse cursor > window root.
// Each level falls through when the previous yields .zero (e.g. GPU-rendered apps with no AX frames).
var ocrBestFrame: CGRect = .zero

if let focused = axEl(appEl, "AXFocusedUIElement" as CFString) {
    ocrBestFrame = nearestFrame(focused)
}

if ocrBestFrame == .zero,
   AXUIElementCopyElementAtPosition(sysEl, Float(mousePos.x), axY, &cursorEl) == .success,
   let el = cursorEl {
    ocrBestFrame = nearestFrame(el)
}

if ocrBestFrame == .zero {
    ocrBestFrame = axFrame(windowRoot)
}

ocrBestFrame = narrowToViewport(ocrBestFrame, cursorX: CGFloat(mousePos.x), cursorAXY: CGFloat(axY))

let ocrFrame = toFrameRect(ocrBestFrame)
emit(Out(app: appName, window_title: windowTitle, content: "", source: "ocr_needed", frame: ocrFrame))
