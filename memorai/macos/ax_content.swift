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

struct Out: Encodable {
    let app: String
    let window_title: String
    let content: String
    let source: String  // "ax_focused" | "ax_web" | "ax_text" | "ocr_needed"
}

func emit(_ out: Out) {
    if let data = try? JSONEncoder().encode(out),
       let s = String(data: data, encoding: .utf8) { print(s) }
}

// MARK: - Main

guard let frontApp = NSWorkspace.shared.frontmostApplication else {
    emit(Out(app: "", window_title: "", content: "", source: "ocr_needed"))
    exit(0)
}

let pid = frontApp.processIdentifier
let appName = frontApp.localizedName ?? ""
let appEl = AXUIElementCreateApplication(pid)

let frontWin = axEl(appEl, "AXFocusedWindow" as CFString)
    ?? axEls(appEl, "AXWindows" as CFString).first
let windowTitle = frontWin.flatMap { axStr($0, "AXTitle" as CFString) } ?? ""
let windowRoot = frontWin ?? appEl

// Strategy 1: keyboard focus — walk up from the focused element to the nearest content container
if let focused = axEl(appEl, "AXFocusedUIElement" as CFString),
   let container = nearestContentAncestor(focused),
   let text = extractText(container) {
    emit(Out(app: appName, window_title: windowTitle, content: text, source: "ax_focused"))
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
    emit(Out(app: appName, window_title: windowTitle, content: text, source: src))
    exit(0)
}

// Strategy 3: largest content element in the frontmost window (fallback)
let candidates = findContentEls(windowRoot)
if let best = candidates.max(by: { axFrameArea($0.el) < axFrameArea($1.el) }),
   let text = extractText(best.el) {
    let src = best.role == "AXWebArea" ? "ax_web" : "ax_text"
    emit(Out(app: appName, window_title: windowTitle, content: text, source: src))
    exit(0)
}

emit(Out(app: appName, window_title: windowTitle, content: "", source: "ocr_needed"))
