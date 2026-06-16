import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct TimelinePane: View {
    @StateObject private var viewModel: TimelineViewModel
    @State private var expandedHour: Int?
    @Environment(\.colorScheme) private var colorScheme

    init(mcpManager: MCPManager) {
        _viewModel = StateObject(wrappedValue: TimelineViewModel(port: mcpManager.mcpPort()))
    }

    private static let dayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEEE, MMM d"
        return f
    }()

    var body: some View {
        VStack(spacing: 0) {
            dateHeader
                .padding(.horizontal, 28)
                .padding(.top, 24)
                .padding(.bottom, 18)

            SoftDivider()

            ScrollView {
                content
                    .padding(.horizontal, 28)
                    .padding(.top, 24)
                    .padding(.bottom, 40)
            }
            .scrollIndicators(.hidden)
            .background(ScrollerHider())
        }
        .task(id: viewModel.selectedDate) {
            expandedHour = nil
            viewModel.load()
        }
    }

    private var dateHeader: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(viewModel.isToday ? "Today" : Self.dayFormatter.string(from: viewModel.selectedDate))
                    .font(.system(size: 22, weight: .bold))
                    .foregroundStyle(.primary)
                Text(statsLine)
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            HStack(spacing: 6) {
                navButton(systemName: "chevron.left", enabled: true) { viewModel.goPrev() }
                Button { viewModel.goToday() } label: {
                    Text("Today")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.primary)
                        .padding(.horizontal, 14)
                        .frame(height: 30)
                        .background(
                            RoundedRectangle(cornerRadius: 9, style: .continuous)
                                .fill(PatchaTheme.bg(for: colorScheme))
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 9, style: .continuous)
                                .stroke(PatchaTheme.softDivider, lineWidth: 1)
                        )
                }
                .buttonStyle(.plain)
                .disabled(viewModel.isToday)
                .opacity(viewModel.isToday ? 0.5 : 1)
                navButton(systemName: "chevron.right", enabled: viewModel.canGoForward) { viewModel.goNext() }
            }
        }
    }

    private var statsLine: String {
        guard viewModel.sessionCount > 0 else { return "No activity recorded" }
        let events = "\(viewModel.eventTotal) event\(viewModel.eventTotal == 1 ? "" : "s")"
        let sessions = "\(viewModel.sessionCount) hour\(viewModel.sessionCount == 1 ? "" : "s")"
        let apps = "\(viewModel.appCount) app\(viewModel.appCount == 1 ? "" : "s")"
        return "\(events) · \(sessions) · \(apps)"
    }

    private func navButton(systemName: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 12, weight: .semibold))
                .frame(width: 30, height: 30)
                .background(
                    RoundedRectangle(cornerRadius: 9, style: .continuous)
                        .fill(PatchaTheme.bg(for: colorScheme))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 9, style: .continuous)
                        .stroke(PatchaTheme.softDivider, lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
        .foregroundStyle(enabled ? .primary : Color.secondary.opacity(0.4))
        .disabled(!enabled)
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading && viewModel.day == nil {
            centeredMessage {
                ProgressView("Loading activity…")
            }
        } else if let error = viewModel.errorText {
            centeredMessage {
                VStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle")
                        .font(.pIconLarge)
                        .foregroundStyle(.secondary)
                    Text(error)
                        .font(.pBody)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                    Button("Retry") { viewModel.load() }
                        .buttonStyle(.plain)
                        .font(.pBodyStrong)
                        .foregroundStyle(PatchaTheme.accent)
                }
            }
        } else if let hours = viewModel.day?.hours, !hours.isEmpty {
            LazyVStack(spacing: 0) {
                ForEach(Array(hours.enumerated()), id: \.element.id) { index, hour in
                    SessionRow(
                        hour: hour,
                        isFirst: index == 0,
                        isLast: index == hours.count - 1,
                        isExpanded: expandedHour == hour.hour,
                        accent: viewModel.color(forApp: hour.apps.first ?? ""),
                        icon: { viewModel.icon(for: $0) },
                        onTap: { toggle(hour.hour) }
                    )
                }
            }
        } else {
            centeredMessage {
                VStack(spacing: 8) {
                    Image(systemName: "moon.zzz")
                        .font(.pIconLarge)
                        .foregroundStyle(.secondary)
                    Text("No activity recorded for this day.")
                        .font(.pBody)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func toggle(_ hour: Int) {
        withAnimation(.easeInOut(duration: 0.18)) {
            expandedHour = (expandedHour == hour) ? nil : hour
        }
    }

    private func centeredMessage<C: View>(@ViewBuilder _ content: () -> C) -> some View {
        HStack {
            Spacer()
            content()
            Spacer()
        }
        .padding(.vertical, 60)
    }
}

private struct SessionRow: View {
    let hour: TimelineHour
    let isFirst: Bool
    let isLast: Bool
    let isExpanded: Bool
    let accent: Color
    let icon: (String) -> NSImage
    let onTap: () -> Void

    @Environment(\.colorScheme) private var colorScheme
    @State private var hover = false

    private let maxIcons = 6

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            timeGutter
            rail
            card
        }
    }

    private var timeGutter: some View {
        VStack(alignment: .trailing, spacing: 3) {
            Text(Self.hourLabel(hour.hour))
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.primary)
            Text("\(hour.eventCount) event\(hour.eventCount == 1 ? "" : "s")")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
        }
        .frame(width: 76, alignment: .trailing)
        .padding(.trailing, 14)
        .padding(.top, 6)
    }

    private var rail: some View {
        ZStack(alignment: .top) {
            if !isFirst {
                Rectangle()
                    .fill(PatchaTheme.softDivider)
                    .frame(width: 2, height: 24)
            }
            if !isLast {
                Rectangle()
                    .fill(accent.opacity(0.33))
                    .frame(width: 2)
                    .frame(maxHeight: .infinity)
                    .padding(.top, 24)
            }
            Circle()
                .fill(accent.opacity(0.33))
                .frame(width: 20, height: 20)
                .overlay(
                    Circle()
                        .fill(PatchaTheme.surface(for: colorScheme))
                        .frame(width: 17, height: 17)
                )
                .overlay(
                    Circle()
                        .fill(accent)
                        .frame(width: 12, height: 12)
                )
                .padding(.top, 7)
        }
        .frame(width: 44)
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: onTap) {
                cardBody
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(PatchaTheme.bg(for: colorScheme))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(hover || isExpanded ? accent.opacity(0.45) : PatchaTheme.softDivider, lineWidth: 1)
        )
        .padding(.bottom, 22)
        .onHover { hover = $0 }
    }

    private var cardBody: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                Text(hour.displayTitle)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                Spacer(minLength: 8)
                Image(systemName: "chevron.down")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .rotationEffect(.degrees(isExpanded ? 180 : 0))
                    .padding(.top, 2)
            }

            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "sparkle")
                    .font(.system(size: 12))
                    .foregroundStyle(PatchaTheme.accent)
                    .padding(.top, 2)
                Text(hour.narrative)
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.top, 8)

            if !hour.apps.isEmpty {
                iconStack
                    .padding(.top, 12)
            }

            if isExpanded {
                breakdown
                    .padding(.top, 14)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .padding(16)
    }

    private var iconStack: some View {
        HStack(spacing: -8) {
            ForEach(Array(hour.apps.prefix(maxIcons).enumerated()), id: \.offset) { _, name in
                Image(nsImage: icon(name))
                    .resizable()
                    .frame(width: 26, height: 26)
                    .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 7, style: .continuous)
                            .stroke(PatchaTheme.bg(for: colorScheme), lineWidth: 2.5)
                    )
                    .help(name)
            }
            if hour.apps.count > maxIcons {
                Text("+\(hour.apps.count - maxIcons)")
                    .font(.pCaptionStrong)
                    .foregroundStyle(.secondary)
                    .frame(width: 26, height: 26)
                    .background(
                        RoundedRectangle(cornerRadius: 7, style: .continuous)
                            .fill(Color.primary.opacity(0.08))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 7, style: .continuous)
                            .stroke(PatchaTheme.bg(for: colorScheme), lineWidth: 2.5)
                    )
            }
        }
    }

    private var breakdown: some View {
        VStack(alignment: .leading, spacing: 12) {
            SoftDivider()
            Text("ACTIVITY BREAKDOWN")
                .font(.system(size: 10, weight: .bold))
                .tracking(0.6)
                .foregroundStyle(.secondary)
            ScrollView(.horizontal, showsIndicators: false) {
                TaskTreeView(root: hour.derivedTree, cardColor: PatchaTheme.bg(for: colorScheme))
                    .padding(.bottom, 4)
            }
        }
    }

    private static func hourLabel(_ hour: Int) -> String {
        var components = DateComponents()
        components.hour = hour
        let calendar = Calendar.current
        guard let date = calendar.date(from: components) else { return "\(hour):00" }
        let formatter = DateFormatter()
        formatter.dateFormat = "h a"
        return formatter.string(from: date)
    }
}

// MARK: - Task tree visualiser

private struct LaidOutNode: Identifiable {
    let id = UUID()
    let label: String
    let depth: Int
    let point: CGPoint
    let isRoot: Bool
    let isLeaf: Bool
}

private struct TreeLayout {
    var nodes: [LaidOutNode] = []
    var edges: [(CGPoint, CGPoint)] = []
    var size: CGSize = .zero
}

private struct TaskTreeView: View {
    let root: TimelineTreeNode
    let cardColor: Color

    private let colW: CGFloat = 168
    private let rowH: CGFloat = 38

    var body: some View {
        let layout = computeLayout()
        Canvas { context, _ in
            // Edges (drawn first so node halos knock them out).
            var edgePath = Path()
            for (a, b) in layout.edges {
                let mx = (a.x + b.x) / 2
                edgePath.move(to: a)
                edgePath.addCurve(to: b, control1: CGPoint(x: mx, y: a.y), control2: CGPoint(x: mx, y: b.y))
            }
            context.stroke(edgePath, with: .color(PatchaTheme.accent.opacity(0.3)), lineWidth: 1.6)

            for node in layout.nodes {
                let diameter: CGFloat = node.isRoot ? 14 : node.isLeaf ? 7.2 : 10
                let rect = CGRect(
                    x: node.point.x - diameter / 2, y: node.point.y - diameter / 2,
                    width: diameter, height: diameter
                )
                let circle = Path(ellipseIn: rect)
                if node.isRoot {
                    context.fill(circle, with: .color(PatchaTheme.accent))
                } else {
                    context.fill(circle, with: .color(node.isLeaf ? leafFill : cardColor))
                    context.stroke(circle, with: .color(PatchaTheme.accent), lineWidth: 1.6)
                }

                let text = Text(node.label)
                    .font(.system(size: node.isRoot ? 13 : 12, weight: node.isRoot ? .semibold : .medium))
                    .foregroundColor(node.isRoot ? rootText : dimText)
                let resolved = context.resolve(text)
                let textSize = resolved.measure(in: CGSize(width: colW, height: rowH))
                let textOrigin = CGPoint(x: node.point.x + 14, y: node.point.y - textSize.height / 2)
                // Halo: knock out the lines behind the label.
                context.fill(
                    Path(CGRect(origin: CGPoint(x: textOrigin.x - 3, y: textOrigin.y),
                                size: CGSize(width: textSize.width + 6, height: textSize.height))),
                    with: .color(cardColor)
                )
                context.draw(resolved, at: textOrigin, anchor: .topLeading)
            }
        }
        .frame(width: max(layout.size.width, 1), height: max(layout.size.height, 1), alignment: .topLeading)
    }

    private var leafFill: Color { Color.primary.opacity(0.12) }
    private var rootText: Color { .primary }
    private var dimText: Color { .secondary }

    private func computeLayout() -> TreeLayout {
        var leaf = 0
        var nodes: [LaidOutNode] = []
        var edges: [(CGPoint, CGPoint)] = []
        var maxDepth = 0

        func assign(_ node: TimelineTreeNode, _ depth: Int) -> CGPoint {
            maxDepth = max(maxDepth, depth)
            let children = node.children ?? []
            let y: CGFloat
            var childPoints: [CGPoint] = []
            if children.isEmpty {
                y = CGFloat(leaf) * rowH + rowH / 2
                leaf += 1
            } else {
                childPoints = children.map { assign($0, depth + 1) }
                y = (childPoints.first!.y + childPoints.last!.y) / 2
            }
            let point = CGPoint(x: CGFloat(depth) * colW + 9, y: y)
            nodes.append(
                LaidOutNode(
                    label: node.label,
                    depth: depth,
                    point: point,
                    isRoot: depth == 0,
                    isLeaf: children.isEmpty
                )
            )
            for child in childPoints {
                edges.append((CGPoint(x: point.x + 9, y: point.y), CGPoint(x: child.x - 4, y: child.y)))
            }
            return point
        }

        _ = assign(root, 0)

        var layout = TreeLayout(nodes: nodes, edges: edges)
        layout.size = CGSize(
            width: CGFloat(maxDepth) * colW + 156,
            height: CGFloat(max(leaf, 1)) * rowH
        )
        return layout
    }
}

private let previewHours: [TimelineHour] = [
    TimelineHour(
        hour: 8, apps: ["Mail", "Notion"],
        summaries: ["Cleared 14 emails and blocked out the day's top priorities in Notion before getting into focus work."],
        categories: ["Planning", "Email"], eventCount: 14, title: "Inbox triage & day planning", tree: nil
    ),
    TimelineHour(
        hour: 9, apps: ["Xcode", "Safari", "Figma"],
        summaries: ["Designed and built the activity timeline view, borrowing the connected-route metaphor from Maps Timeline."],
        categories: ["Development", "Design"], eventCount: 42, title: "Building the activity Timeline", tree: nil
    ),
    TimelineHour(
        hour: 11, apps: ["Terminal", "Xcode"],
        summaries: ["Wired up the app-level permission toggles and tested the rescan flow directly in the shell."],
        categories: ["Development"], eventCount: 30, title: "Permissions system deep work", tree: nil
    ),
]

private struct SessionRowPreview: View {
    @State private var expanded: Int? = 9
    private let fallback = NSWorkspace.shared.icon(for: .applicationBundle)

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                ForEach(Array(previewHours.enumerated()), id: \.element.id) { index, hour in
                    SessionRow(
                        hour: hour,
                        isFirst: index == 0,
                        isLast: index == previewHours.count - 1,
                        isExpanded: expanded == hour.hour,
                        accent: hour.hour == 8 ? Color(hex: "2AA4FF") : (hour.hour == 9 ? Color(hex: "A259FF") : Color(hex: "25C065")),
                        icon: { _ in fallback }
                    ) {
                        withAnimation(.easeInOut(duration: 0.18)) {
                            expanded = expanded == hour.hour ? nil : hour.hour
                        }
                    }
                }
            }
            .padding(28)
        }
    }
}

#Preview("Dark") {
    SessionRowPreview()
        .frame(width: 620, height: 760)
        .background(PatchaTheme.surface(for: .dark))
        .preferredColorScheme(.dark)
}

#Preview("Light") {
    SessionRowPreview()
        .frame(width: 620, height: 760)
        .background(PatchaTheme.surface(for: .light))
        .preferredColorScheme(.light)
}
