// Event observer — long-lived macOS notification subscriber.
//
// Emits one JSON trigger per line on stdout so the Rust daemon can capture
// context switches instantly instead of discovering them via polling:
//   {"type":"app_switch","app_name":"...","window_title":"...","timestamp":"..."}
//
// Trigger types: app_switch, space_switch, title_change, screen_lock, screen_unlock.
// Mirrors the persistent-helper pattern of ax_content / mobileclip.

import AppKit
import ApplicationServices
import Foundation

// ---------------------------------------------------------------------------
// Output
// ---------------------------------------------------------------------------

func iso8601Now() -> String {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f.string(from: Date())
}

func emit(_ type: String, app: String, title: String) {
    let obj: [String: Any] = [
        "type": type,
        "app_name": app,
        "window_title": title,
        "timestamp": iso8601Now(),
    ]
    guard let data = try? JSONSerialization.data(withJSONObject: obj),
          let line = String(data: data, encoding: .utf8) else { return }
    print(line)
    fflush(stdout)
}

// ---------------------------------------------------------------------------
// Frontmost app + focused-window title
// ---------------------------------------------------------------------------

func frontmostAppName() -> String {
    NSWorkspace.shared.frontmostApplication?.localizedName ?? ""
}

func focusedWindowTitle(pid: pid_t) -> String {
    let appElem = AXUIElementCreateApplication(pid)
    var winRef: CFTypeRef?
    guard AXUIElementCopyAttributeValue(appElem, kAXFocusedWindowAttribute as CFString, &winRef) == .success,
          let win = winRef else { return "" }
    let winElem = win as! AXUIElement
    var titleRef: CFTypeRef?
    guard AXUIElementCopyAttributeValue(winElem, kAXTitleAttribute as CFString, &titleRef) == .success,
          let title = titleRef as? String else { return "" }
    return title
}

// ---------------------------------------------------------------------------
// AX title-change watcher (rebinds to the frontmost app on every switch)
// ---------------------------------------------------------------------------

final class TitleWatcher {
    private var observer: AXObserver?
    private var appElem: AXUIElement?
    private var pid: pid_t = 0

    func retarget(to pid: pid_t) {
        teardown()
        self.pid = pid
        let app = AXUIElementCreateApplication(pid)
        self.appElem = app

        var obs: AXObserver?
        let callback: AXObserverCallback = { _, _, _, _ in
            // Any focused-window / title change -> re-read the title and emit.
            let app = frontmostAppName()
            let title = focusedWindowTitle(pid: watcher.pid)
            emit("title_change", app: app, title: title)
        }
        guard AXObserverCreate(pid, callback, &obs) == .success, let obs else { return }
        self.observer = obs

        AXObserverAddNotification(obs, app, kAXFocusedWindowChangedNotification as CFString, nil)
        AXObserverAddNotification(obs, app, kAXTitleChangedNotification as CFString, nil)
        // Watch the currently focused window's title too.
        var winRef: CFTypeRef?
        if AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute as CFString, &winRef) == .success,
           let win = winRef {
            AXObserverAddNotification(obs, win as! AXUIElement, kAXTitleChangedNotification as CFString, nil)
        }
        CFRunLoopAddSource(CFRunLoopGetCurrent(), AXObserverGetRunLoopSource(obs), .defaultMode)
    }

    private func teardown() {
        if let obs = observer {
            CFRunLoopRemoveSource(CFRunLoopGetCurrent(), AXObserverGetRunLoopSource(obs), .defaultMode)
        }
        observer = nil
        appElem = nil
    }
}

let watcher = TitleWatcher()

// ---------------------------------------------------------------------------
// Subscriptions
// ---------------------------------------------------------------------------

let ws = NSWorkspace.shared
let wsCenter = ws.notificationCenter

wsCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { note in
    let appInfo = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
    let app = appInfo?.localizedName ?? frontmostAppName()
    let pid = appInfo?.processIdentifier ?? (NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0)
    if pid != 0 { watcher.retarget(to: pid) }
    emit("app_switch", app: app, title: focusedWindowTitle(pid: pid))
}

wsCenter.addObserver(forName: NSWorkspace.activeSpaceDidChangeNotification, object: nil, queue: .main) { _ in
    let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    emit("space_switch", app: frontmostAppName(), title: focusedWindowTitle(pid: pid))
}

// Screen lock/unlock are delivered via the distributed notification center.
let dist = DistributedNotificationCenter.default()
dist.addObserver(forName: NSNotification.Name("com.apple.screenIsLocked"), object: nil, queue: .main) { _ in
    emit("screen_lock", app: "", title: "")
}
dist.addObserver(forName: NSNotification.Name("com.apple.screenIsUnlocked"), object: nil, queue: .main) { _ in
    let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    emit("screen_unlock", app: frontmostAppName(), title: focusedWindowTitle(pid: pid))
}

// Bind the title watcher to whatever is frontmost at startup.
if let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier {
    watcher.retarget(to: pid)
}

RunLoop.main.run()
