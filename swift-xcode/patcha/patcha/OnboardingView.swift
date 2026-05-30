import SwiftUI
import AppKit

enum OnboardingStep: Int, CaseIterable {
    case profile
    case permissions
    case privacy
}

struct OnboardingView: View {
    @ObservedObject var store: SettingsStore
    @Environment(\.colorScheme) private var colorScheme
    @State private var step: OnboardingStep
    @StateObject private var appsVM: AppPermissionsViewModel

    init(store: SettingsStore) {
        self.store = store
        self._appsVM = StateObject(wrappedValue: AppPermissionsViewModel(store: store))
        let startStep: OnboardingStep
        if !store.onboardingProfileDone {
            startStep = .profile
        } else if !store.onboardingPermissionsDone {
            startStep = .permissions
        } else {
            startStep = .privacy
        }
        self._step = State(initialValue: startStep)
    }

    var body: some View {
        ZStack {
            PatchaTheme.bg(for: colorScheme).ignoresSafeArea()

            switch step {
            case .profile:
                ProfileOnboardingStep(store: store, onContinue: completeProfile)
            case .permissions:
                PermissionsOnboardingStep(onContinue: completePermissions)
            case .privacy:
                PrivacyOnboardingStep(store: store, appsVM: appsVM, onContinue: completePrivacy)
            }
        }
        .background(OnboardingWindowSizer(size: NSSize(width: 900, height: 620)))
    }

    private func completeProfile() {
        store.onboardingProfileDone = true
        store.save {}
        step = .permissions
    }

    private func completePermissions() {
        store.onboardingPermissionsDone = true
        store.save {}
        step = .privacy
    }

    private func completePrivacy() {
        store.onboardingPrivacyDone = true
        store.onboardingCompleted = true
        store.save {}
        sendOnboardingAnalytics()
    }

    private func sendOnboardingAnalytics() {
        // TBD: POST analytics payload to API once endpoint is defined
        let payload: [String: Any] = [
            "role": store.profileRole,
            "source": store.profileSource,
            "tools": store.profileTools,
            "feedback_opt_in": store.profileFeedbackOptIn,
        ]
        NSLog("[Onboarding] analytics payload: %@", payload.description)
    }
}

private struct ProfileOnboardingStep: View {
    @ObservedObject var store: SettingsStore
    let onContinue: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    @State private var role: String = ""
    @State private var source: String = ""
    @State private var tools: Set<String> = []
    @State private var feedbackOptIn: Bool = true

    private let roles = ["Software engineer", "Designer", "Product manager", "Engineering manager", "Founder", "Other"]
    private let sources = ["Twitter / X", "Hacker News", "A friend", "Reddit", "Product Hunt", "YouTube", "Other"]
    private let aiTools = ["Cursor", "Claude Code", "Claude Desktop", "ChatGPT", "Windsurf", "Zed", "Other"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 36) {
                header
                intro
                question(number: "01", title: "What do you do?") {
                    PillRow(options: roles, selected: role.isEmpty ? [] : [role]) { tapped in
                        role = role == tapped ? "" : tapped
                    }
                }
                question(number: "02", title: "Where'd you hear about Patcha?") {
                    PillRow(options: sources, selected: source.isEmpty ? [] : [source]) { tapped in
                        source = source == tapped ? "" : tapped
                    }
                }
                question(number: "03", title: "Which AI tools do you use today?", subtitle: "(pick all that apply)") {
                    PillRow(options: aiTools, selected: tools) { tapped in
                        if tools.contains(tapped) { tools.remove(tapped) } else { tools.insert(tapped) }
                    }
                }
                consentCard
                footer
            }
            .padding(.horizontal, 80)
            .padding(.vertical, 48)
            .frame(maxWidth: 920, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .scrollIndicators(.hidden)
        .onAppear(perform: prefill)
    }

    private var header: some View {
        HStack(spacing: 8) {
            if let icon = NSApp.applicationIconImage {
                Image(nsImage: icon)
                    .resizable()
                    .frame(width: 28, height: 28)
            }
            Text("patcha")
                .font(.pBrandmark)
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("PROFILE")
                .font(.pEyebrow)
                .tracking(2)
                .foregroundStyle(.secondary)

            Text("Hello — we'd like to \(Text("get to know you.").font(.pDisplayItalic).foregroundColor(PatchaTheme.accent))")
                .font(.pDisplay)
                .foregroundColor(.secondary)
                .lineSpacing(2)

            Text("Three questions. Helps prioritize what we build next.")
                .font(.pCallout)
                .foregroundStyle(.secondary)
        }
        .padding(.top, 8)
    }

    @ViewBuilder
    private func question<Content: View>(number: String, title: String, subtitle: String? = nil, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(number)
                    .font(.pNumeral)
                    .foregroundStyle(.secondary)
                Text(title)
                    .font(.pSubheadline)
                if let subtitle {
                    Text(subtitle)
                        .font(.pNoteItalic)
                        .foregroundStyle(.secondary)
                }
            }
            content()
                .padding(.leading, 36)
        }
    }

    private var consentCard: some View {
        HStack(alignment: .top, spacing: 14) {
            Button {
                feedbackOptIn.toggle()
            } label: {
                ZStack {
                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .fill(feedbackOptIn ? PatchaTheme.accent : Color.primary.opacity(0.08))
                        .frame(width: 28, height: 28)
                    if feedbackOptIn {
                        Image(systemName: "checkmark")
                            .font(.pIconMedium)
                            .foregroundColor(PatchaTheme.bg(for: colorScheme))
                    }
                }
            }
            .buttonStyle(.plain)

            VStack(alignment: .leading, spacing: 4) {
                Text("Reach out for feedback when it matters.")
                    .font(.pCalloutStrong)
                Text("Patcha is early. Your replies shape what we build next — and we'll only ever email you when it really matters. Never for marketing.")
                    .font(.pBody)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(18)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color.primary.opacity(0.04))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(PatchaTheme.softDivider, lineWidth: 1)
        )
    }

    private var footer: some View {
        HStack {
            Spacer()
            Button("Continue") { save() }
                .buttonStyle(OnboardingPrimaryButtonStyle(isDisabled: !isValid))
                .disabled(!isValid)
        }
    }

    private var isValid: Bool {
        !role.isEmpty && !source.isEmpty
    }

    private func prefill() {
        if !store.profileRole.isEmpty { role = store.profileRole }
        if !store.profileSource.isEmpty { source = store.profileSource }
        if !store.profileTools.isEmpty {
            tools = Set(store.profileTools.split(separator: ",").map(String.init))
        }
        feedbackOptIn = store.profileFeedbackOptIn
    }

    private func save() {
        store.profileRole = role
        store.profileSource = source
        store.profileTools = tools.sorted().joined(separator: ",")
        store.profileFeedbackOptIn = feedbackOptIn
        onContinue()
    }
}

private struct PermissionsOnboardingStep: View {
    let onContinue: () -> Void
    @Environment(\.colorScheme) private var colorScheme
    @State private var status: PermissionStatus = PermissionsManager.snapshot()
    @State private var pollTimer: Timer?
    @State private var activationObserver: NSObjectProtocol?
    @State private var fdaArmed: Bool = PermissionsManager.fdaProbingArmed()
    @State private var accessibilityArmed: Bool = false
    @State private var screenRecordingArmed: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 36) {
                header
                intro
                VStack(spacing: 14) {
                    permissionRow(
                        title: "Screen Recording",
                        description: "Reads what's on screen across your apps.",
                        systemImage: "display.fill",
                        granted: status.screenRecording,
                        action: grantScreenRecording,
                        secondaryAction: screenRecordingArmed && !status.screenRecording ? ("Re-check", { refresh() }) : nil
                    )
                    permissionRow(
                        title: "Accessibility",
                        description: "Reads the window you're focused on.",
                        systemImage: "dot.circle.fill",
                        granted: status.accessibility,
                        action: grantAccessibility,
                        secondaryAction: accessibilityArmed && !status.accessibility ? ("Re-check", { refresh() }) : nil
                    )
                    permissionRow(
                        title: "Full Disk Access",
                        description: "Reads your browser and terminal history.",
                        systemImage: "doc.text.fill",
                        granted: status.fullDiskAccess,
                        action: grantFullDiskAccess,
                        secondaryAction: fdaArmed && !status.fullDiskAccess ? ("Re-check", { refresh() }) : nil
                    )
                }
                .padding(.top, 8)

                footer
            }
            .padding(.horizontal, 80)
            .padding(.vertical, 48)
            .frame(maxWidth: 920, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .scrollIndicators(.hidden)
        .onAppear {
            refresh()
            startPolling()
            activationObserver = NotificationCenter.default.addObserver(
                forName: NSApplication.didBecomeActiveNotification,
                object: nil,
                queue: .main
            ) { _ in
                refresh()
            }
        }
        .onDisappear {
            pollTimer?.invalidate()
            pollTimer = nil
            if let token = activationObserver {
                NotificationCenter.default.removeObserver(token)
                activationObserver = nil
            }
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            if let icon = NSApp.applicationIconImage {
                Image(nsImage: icon)
                    .resizable()
                    .frame(width: 28, height: 28)
            }
            Text("patcha")
                .font(.pBrandmark)
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("ALMOST THERE")
                .font(.pEyebrow)
                .tracking(2)
                .foregroundStyle(.secondary)

            Text("Patcha needs \(Text("three permissions").font(.pDisplayItalic).foregroundColor(PatchaTheme.accent)) to get to work.")
                .font(.pDisplay)
                .foregroundColor(.secondary)
                .lineSpacing(2)
        }
        .padding(.top, 8)
    }

    @ViewBuilder
    private func permissionRow(title: String, description: String, systemImage: String, granted: Bool, action: @escaping () -> Void, secondaryAction: (String, () -> Void)? = nil) -> some View {
        HStack(spacing: 16) {
            ZStack {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color.primary.opacity(0.06))
                    .frame(width: 48, height: 48)
                Image(systemName: systemImage)
                    .font(.pIconXLarge)
                    .foregroundColor(.primary.opacity(0.7))
            }

            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.pSubheadline)
                Text(description)
                    .font(.pBody)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 16)

            if granted {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(PatchaTheme.accent)
                    Text("Granted")
                        .font(.pBody)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 8)
            } else {
                VStack(alignment: .trailing, spacing: 4) {
                    Button("Grant", action: action)
                        .buttonStyle(GrantButtonStyle())
                    if let (label, secondary) = secondaryAction {
                        Button(label, action: secondary)
                            .buttonStyle(.plain)
                            .font(.pCaption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(18)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color.primary.opacity(0.04))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(PatchaTheme.softDivider, lineWidth: 1)
        )
    }

    private var footer: some View {
        VStack(spacing: 14) {
            Text("All three are required.")
                .font(.pNoteItalic)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .center)
            HStack {
                Spacer()
                Button("Continue") { onContinue() }
                    .buttonStyle(OnboardingPrimaryButtonStyle(isDisabled: !status.allGranted))
                    .disabled(!status.allGranted)
            }
        }
        .padding(.top, 8)
    }

    private func refresh() {
        status = PermissionsManager.snapshot()
    }

    private func startPolling() {
        pollTimer?.invalidate()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { _ in
            DispatchQueue.main.async { refresh() }
        }
    }

    private func grantScreenRecording() {
        screenRecordingArmed = true
        PermissionsManager.requestScreenRecording()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { refresh() }
    }

    private func grantAccessibility() {
        accessibilityArmed = true
        PermissionsManager.requestAccessibility()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { refresh() }
    }

    private func grantFullDiskAccess() {
        PermissionsManager.armFDAProbing()
        fdaArmed = true
        PermissionsManager.openFullDiskAccessSettings()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { refresh() }
    }
}

private struct PrivacyOnboardingStep: View {
    @ObservedObject var store: SettingsStore
    @ObservedObject var appsVM: AppPermissionsViewModel
    let onContinue: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                header
                intro
                infoCard
                AppPermissionsPane(store: store, viewModel: appsVM, embedded: true)
                footer
            }
            .padding(.horizontal, 80)
            .padding(.vertical, 48)
            .frame(maxWidth: 920, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .scrollIndicators(.hidden)
        .onAppear {
            if appsVM.apps.isEmpty { appsVM.scan() }
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            if let icon = NSApp.applicationIconImage {
                Image(nsImage: icon)
                    .resizable()
                    .frame(width: 28, height: 28)
            }
            Text("patcha")
                .font(.pBrandmark)
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("PRIVACY")
                .font(.pEyebrow)
                .tracking(2)
                .foregroundStyle(.secondary)

            Text("Decide what \(Text("stays private.").font(.pDisplayItalic).foregroundColor(PatchaTheme.accent))")
                .font(.pDisplay)
                .foregroundColor(.secondary)
                .lineSpacing(2)

            Text("You've given Patcha access to your machine. Now take some of it back. These settings tell Patcha what to leave alone.")
                .font(.pCallout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 8)
    }

    private var infoCard: some View {
        HStack(alignment: .top, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .fill(PatchaTheme.accent)
                    .frame(width: 36, height: 36)
                Image(systemName: "checkmark.shield.fill")
                    .font(.pIconLarge)
                    .foregroundColor(PatchaTheme.bg(for: colorScheme))
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("Passwords, banking, and sensitive apps are off-limits by default.")
                    .font(.pCalloutStrong)
                Text("Patcha auto-detects and ignores password managers, banking apps, and known financial sites — nothing to configure.")
                    .font(.pBody)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 12)

            Button(action: {}) {
                Text("How this works →")
                    .font(.pBody)
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(18)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(PatchaTheme.accent.opacity(0.10))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(PatchaTheme.accent.opacity(0.35), lineWidth: 1)
        )
    }

    private var footer: some View {
        HStack {
            Spacer()
            Button("Continue") { onContinue() }
                .buttonStyle(OnboardingPrimaryButtonStyle(isDisabled: false))
        }
        .padding(.top, 8)
    }
}

private struct GrantButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.pBodyStrong)
            .foregroundColor(.white)
            .padding(.horizontal, 22)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(Color.black.opacity(configuration.isPressed ? 0.7 : 0.9))
            )
    }
}

private struct PillRow: View {
    let options: [String]
    let selected: Set<String>
    let onTap: (String) -> Void

    var body: some View {
        FlowLayout(spacing: 10, lineSpacing: 10) {
            ForEach(options, id: \.self) { option in
                PillButton(label: option, isSelected: selected.contains(option)) {
                    onTap(option)
                }
            }
        }
    }
}

private struct PillButton: View {
    let label: String
    let isSelected: Bool
    let action: () -> Void
    @Environment(\.colorScheme) private var colorScheme
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(.pBody)
                .foregroundColor(isSelected ? PatchaTheme.bg(for: colorScheme) : .primary)
                .padding(.horizontal, 16)
                .padding(.vertical, 9)
                .background(
                    Capsule(style: .continuous)
                        .fill(background)
                )
                .overlay(
                    Capsule(style: .continuous)
                        .stroke(isSelected ? Color.clear : PatchaTheme.softDivider, lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: 0.12), value: isSelected)
        .animation(.easeOut(duration: 0.12), value: hovering)
    }

    private var background: Color {
        if isSelected { return PatchaTheme.accent }
        if hovering { return Color.primary.opacity(0.08) }
        return Color.primary.opacity(0.04)
    }
}

struct OnboardingPrimaryButtonStyle: ButtonStyle {
    let isDisabled: Bool
    @Environment(\.colorScheme) private var colorScheme

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.pCalloutStrong)
            .foregroundColor(PatchaTheme.bg(for: colorScheme))
            .padding(.horizontal, 24)
            .padding(.vertical, 11)
            .background(
                Capsule(style: .continuous)
                    .fill(PatchaTheme.accent.opacity(isDisabled ? 0.4 : configuration.isPressed ? 0.8 : 1.0))
            )
    }
}

struct FlowLayout: Layout {
    var spacing: CGFloat = 8
    var lineSpacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var lineHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > maxWidth {
                x = 0
                y += lineHeight + lineSpacing
                lineHeight = 0
            }
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
        return CGSize(width: maxWidth == .infinity ? x : maxWidth, height: y + lineHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x: CGFloat = bounds.minX
        var y: CGFloat = bounds.minY
        var lineHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX {
                x = bounds.minX
                y += lineHeight + lineSpacing
                lineHeight = 0
            }
            subview.place(at: CGPoint(x: x, y: y), anchor: .topLeading, proposal: ProposedViewSize(size))
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
    }
}

private struct OnboardingWindowSizer: NSViewRepresentable {
    let size: NSSize

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { resize(from: view) }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async { resize(from: nsView) }
    }

    private func resize(from view: NSView) {
        guard let window = view.window else { return }
        if window.frame.size == size { return }
        var frame = window.frame
        let dx = size.width - frame.size.width
        let dy = size.height - frame.size.height
        frame.size = size
        frame.origin.x -= dx / 2
        frame.origin.y -= dy
        window.minSize = NSSize(width: 800, height: 560)
        window.setFrame(frame, display: true, animate: true)
    }
}
