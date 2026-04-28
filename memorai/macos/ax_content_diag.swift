// ax_content_diag.swift — diagnostic-only additions.
// Run via run_diag.sh which prepends ax_content.swift helpers before compiling.
// Do NOT add functions already in ax_content.swift here.

import AppKit
import ApplicationServices

// MARK: - Diag-only helpers

// Scan for ARIA landmark subroles in the web area tree.
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

// MARK: - Diagnostic output structs

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

guard let frontApp = NSWorkspace.shared.frontmostApplication else {
    fputs("ERROR: no frontmost application\n", stderr)
    exit(1)
}

let pid = frontApp.processIdentifier
let appName = frontApp.localizedName ?? ""
let appEl = AXUIElementCreateApplication(pid)

let frontWin = axEl(appEl, "AXFocusedWindow" as CFString)
    ?? axEls(appEl, "AXWindows" as CFString).first
let windowTitle = frontWin.flatMap { axStr($0, "AXTitle" as CFString) } ?? ""
let windowRoot = frontWin ?? appEl

var strategies: [DiagResult.StrategyResult] = []
var winner = "ocr_needed"
var winnerContent: String? = nil

// ── Strategy 1: keyboard focus walk-up ──────────────────────────────────────
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

// ── Strategy 2: cursor position ──────────────────────────────────────────────
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

// ── Strategy 3: BFS + largest area ──────────────────────────────────────────
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

// ── Web area analysis ────────────────────────────────────────────────────────
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

// ── Emit ─────────────────────────────────────────────────────────────────────
let result = DiagResult(
    app: appName,
    window_title: windowTitle,
    cursor_pos: cursorDesc,
    strategies: strategies,
    bfs_candidates: bfsCandidates,
    web_area_analysis: webAnalysis,
    winner: winner,
    full_content: winnerContent
)

let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
if let data = try? encoder.encode(result),
   let s = String(data: data, encoding: .utf8) {
    print(s)
}
