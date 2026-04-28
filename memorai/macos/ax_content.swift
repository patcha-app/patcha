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

func axFrameArea(_ el: AXUIElement) -> Double {
    var val: CFTypeRef?
    guard cfGet(el, "AXFrame" as CFString, &val), let v = val,
          CFGetTypeID(v) == AXValueGetTypeID() else { return 0 }
    var rect = CGRect.zero
    AXValueGetValue(v as! AXValue, .cgRect, &rect)
    return Double(rect.width * rect.height)
}

// Prefer the visible portion of a text element over the full value to avoid
// sending megabytes of an open file to the LLM.
func extractText(_ el: AXUIElement, maxChars: Int = 4000) -> String? {
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

// Strategy 1: focused element — user is actively typing/editing
if let focused = axEl(appEl, "AXFocusedUIElement" as CFString),
   let role = axStr(focused, "AXRole" as CFString),
   ["AXTextArea", "AXWebArea"].contains(role),
   let text = extractText(focused) {
    emit(Out(app: appName, window_title: windowTitle, content: text, source: "ax_focused"))
    exit(0)
}

// Strategy 2: largest content element in the frontmost window
let candidates = findContentEls(windowRoot)
if let best = candidates.max(by: { axFrameArea($0.el) < axFrameArea($1.el) }),
   let text = extractText(best.el) {
    let src = best.role == "AXWebArea" ? "ax_web" : "ax_text"
    emit(Out(app: appName, window_title: windowTitle, content: text, source: src))
    exit(0)
}

emit(Out(app: appName, window_title: windowTitle, content: "", source: "ocr_needed"))
