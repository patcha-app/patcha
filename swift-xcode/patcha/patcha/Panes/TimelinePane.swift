import AppKit
import SwiftUI

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
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                dateHeader
                content
            }
            .padding(.horizontal, 20)
            .padding(.top, 20)
            .padding(.bottom, 20)
        }
        .scrollIndicators(.hidden)
        .background(ScrollerHider())
        .task(id: viewModel.selectedDate) {
            expandedHour = nil
            viewModel.load()
        }
    }

    private var dateHeader: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                SectionHeader(title: "Timeline")
                Text(viewModel.isToday ? "Today" : Self.dayFormatter.string(from: viewModel.selectedDate))
                    .font(.pHeadline)
            }
            Spacer()
            HStack(spacing: 4) {
                navButton(systemName: "chevron.left", enabled: true) { viewModel.goPrev() }
                navButton(systemName: "chevron.right", enabled: viewModel.canGoForward) { viewModel.goNext() }
            }
        }
    }

    private func navButton(systemName: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.pIconSmall)
                .frame(width: 30, height: 30)
                .background(
                    Circle().fill(PatchaTheme.bg(for: colorScheme))
                )
                .overlay(
                    Circle().stroke(PatchaTheme.softDivider, lineWidth: 1)
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
            LazyVStack(spacing: 12) {
                ForEach(hours) { hour in
                    TimelineHourBlock(
                        hour: hour,
                        isExpanded: expandedHour == hour.hour,
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

private struct TimelineHourBlock: View {
    let hour: TimelineHour
    let isExpanded: Bool
    let icon: (String) -> NSImage
    let onTap: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    private let maxIcons = 6

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Text(Self.hourLabel(hour.hour))
                .font(.pCaptionStrong)
                .foregroundStyle(.secondary)
                .frame(width: 56, alignment: .leading)
                .padding(.top, 8)

            VStack(alignment: .leading, spacing: 0) {
                Button(action: onTap) {
                    appRow
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)

                if isExpanded {
                    summaryCard
                        .padding(.top, 2)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(PatchaTheme.bg(for: colorScheme))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(PatchaTheme.softDivider, lineWidth: 1)
            )
        }
    }

    private var appRow: some View {
        HStack(spacing: 10) {
            HStack(spacing: -6) {
                ForEach(Array(hour.apps.prefix(maxIcons).enumerated()), id: \.offset) { _, name in
                    Image(nsImage: icon(name))
                        .resizable()
                        .frame(width: 26, height: 26)
                        .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(Color.primary.opacity(0.08), lineWidth: 0.5))
                        .overlay(
                            RoundedRectangle(cornerRadius: 7, style: .continuous)
                                .stroke(PatchaTheme.softDivider, lineWidth: 1)
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
                }
            }

            VStack(alignment: .leading, spacing: 2) {
                Text(hour.apps.first ?? "Activity")
                    .font(.pBodyStrong)
                    .lineLimit(1)
                Text("\(hour.eventCount) event\(hour.eventCount == 1 ? "" : "s")")
                    .font(.pCaption)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 8)

            Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                .font(.pIconSmall)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
    }

    private var summaryCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            SoftDivider().padding(.horizontal, 14)

            if !hour.categories.isEmpty {
                HStack(spacing: 6) {
                    ForEach(hour.categories, id: \.self) { category in
                        Text(category)
                            .font(.pCaptionStrong)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(
                                Capsule().fill(PatchaTheme.accent.opacity(0.12))
                            )
                            .foregroundStyle(PatchaTheme.accent)
                    }
                }
                .padding(.horizontal, 14)
            }

            if hour.summaries.isEmpty {
                Text("No summary captured for this hour.")
                    .font(.pBody)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 14)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(hour.summaries.enumerated()), id: \.offset) { _, summary in
                        HStack(alignment: .top, spacing: 8) {
                            Circle()
                                .fill(PatchaTheme.accent.opacity(0.6))
                                .frame(width: 4, height: 4)
                                .padding(.top, 6)
                            Text(summary)
                                .font(.pBody)
                                .foregroundStyle(.primary.opacity(0.85))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .padding(.horizontal, 14)
            }
        }
        .padding(.bottom, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
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
