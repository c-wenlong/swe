import Charts
import SWECore
import SwiftUI

/// One metric's trend lines: a line per harness in the harness's colour,
/// its icon riding the line's right end, over the window ComputeModel's
/// trend sampler has filled. The Compute page shows one at a time behind
/// tabs; the popup's Compute tab stacks them. Charts that share a
/// `hovered` binding draw the same hover rule, and the readout bubble
/// shows only on the chart under the pointer.
struct TrendChart: View {
    @Environment(AppModel.self) private var app
    var metric: TrendMetric
    /// The harnesses to draw, in the order the readout lists them.
    var harnesses: [String]
    var height: CGFloat = 180
    var showsXAxis = true
    /// The RAM chart's dashed flag line and its label.
    var showsFlag = true
    /// Read only by the hover layer: the chart body never touches it, so
    /// a mouse move redraws the rule and bubble, not the lines.
    @Binding var hovered: Date?

    private var model: ComputeModel { app.compute }
    /// The popup's stacked size: fewer ticks, smaller icons.
    private var compact: Bool { height < 120 }

    /// The one x range every series is clipped to: `window` intervals
    /// ending at the newest recorded point, so a stopped harness's line
    /// slides off the left edge instead of holding it open.
    static func window(_ model: ComputeModel) -> ClosedRange<Date>? {
        guard let end = model.history.latest else { return nil }
        return model.history.window(endingAt: end, interval: model.trendInterval)
    }

    /// Each harness's points in the visible window that have a reading,
    /// filtered once per body: the marks, the y scale and the hover
    /// bubble all read this one copy.
    private func plotted() -> [String: [TrendPoint]] {
        guard let range = Self.window(model) else { return [:] }
        return Dictionary(harnesses.map { ($0, model.history.window(for: $0, in: range).filter(metric.reads)) },
                          uniquingKeysWith: { first, _ in first })
    }

    /// The y axis's fixed top, from the plotted values only. Nothing the
    /// hover state touches feeds it, so the scale cannot move under the
    /// cursor.
    private func yTop(_ plotted: [String: [TrendPoint]]) -> Double {
        metric.top(plotted.values.flatMap { $0.map(metric.value(of:)) },
                   flagBytes: model.limits.memoryBytes)
    }

    static func formatted(_ value: Double, _ metric: TrendMetric) -> String {
        switch metric {
        case .ram: ComputeSummary.bytes(UInt64(max(0, value) * Double(UInt64(1) << 30)))
        case .cpu, .gpu: "\(ComputeSummary.percent(value))%"
        case .temperature: TemperatureChart.formatted(value)
        }
    }

    private func colour(of harness: String) -> Color {
        Color(rgb: HarnessColor.of(harness))
    }

    var body: some View {
        let limitGB = Double(model.limits.memoryBytes) / Double(UInt64(1) << 30)
        // The chart only renders once a point exists, so latest is set.
        let domain = Self.window(model) ?? Date.now.addingTimeInterval(-60)...Date.now
        let plotted = plotted()
        Chart {
            ForEach(harnesses, id: \.self) { harness in
                let points = plotted[harness] ?? []
                ForEach(points, id: \.at) { point in
                    LineMark(x: .value("Time", point.at), y: .value("Value", metric.value(of: point)),
                             series: .value("Harness", harness))
                        .foregroundStyle(colour(of: harness))
                        .interpolationMethod(.monotone)
                        .lineStyle(StrokeStyle(lineWidth: 2))
                }
                if let last = points.last {
                    PointMark(x: .value("Time", last.at), y: .value("Value", metric.value(of: last)))
                        .foregroundStyle(colour(of: harness))
                        .symbolSize(16)
                        .annotation(position: .trailing, alignment: .center,
                                    overflowResolution: .init(x: .disabled, y: .disabled)) {
                            HarnessIcon(harness: harness, size: compact ? 12 : 16)
                        }
                }
            }
            if metric == .ram && showsFlag {
                RuleMark(y: .value("Flag", limitGB))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4]))
                    .foregroundStyle(Theme.warning)
                    .annotation(position: .top, alignment: .leading, spacing: 4) {
                        Text("Flag above \(ComputeSummary.bytes(model.limits.memoryBytes))")
                            .font(Theme.caption).foregroundStyle(Theme.warning)
                    }
            }
        }
        .chartXScale(domain: domain)
        .chartYScale(domain: 0...yTop(plotted))
        .chartXAxis {
            if showsXAxis {
                AxisMarks(values: .automatic(desiredCount: compact ? 4 : 6)) { value in
                    AxisGridLine()
                    AxisTick()
                    if ChartTimeAxis.labels(value.as(Date.self), in: domain, compact: compact) { AxisValueLabel() }
                }
            }
        }
        // A compact chart puts its scale on the left, so the icons at the
        // lines' right ends have the trailing edge to themselves.
        .chartYAxis {
            AxisMarks(position: compact ? .leading : .automatic,
                      values: .automatic(desiredCount: compact ? 3 : 5)) { value in
                AxisGridLine()
                AxisValueLabel { axisLabel(value) }
            }
        }
        .padding(.trailing, compact ? 16 : 22)
        .frame(height: height)
        // The rule and the bubble live in the overlay, where no chart
        // mark can grow the scale and a hover never rebuilds the marks.
        .chartOverlay { proxy in
            ChartHoverLayer(proxy: proxy, hovered: $hovered) { time in bubble(at: time, plotted: plotted) }
        }
    }

    private func axisLabel(_ value: AxisValue) -> some View {
        let number = value.as(Double.self) ?? 0
        if metric == .ram {
            return Text("\(number.formatted(.number.precision(.fractionLength(compact ? 0 : 1)))) GB")
        }
        return Text("\(Int(number.rounded()))%")
    }

    /// Each harness's value at the point nearest the hovered time.
    private func bubble(at time: Date, plotted: [String: [TrendPoint]]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(harnesses, id: \.self) { harness in
                if let point = (plotted[harness] ?? []).min(by: {
                    abs($0.at.timeIntervalSince(time)) < abs($1.at.timeIntervalSince(time))
                }) {
                    HStack(spacing: 6) {
                        HarnessIcon(harness: harness, size: 12)
                        Text(UsageFormat.harness(harness))
                        Spacer()
                        Text(Self.formatted(metric.value(of: point), metric)).monospacedDigit()
                    }
                }
            }
        }
        .font(Theme.caption).foregroundStyle(Theme.textPrimary)
        .padding(8)
        .background(Theme.panel, in: .rect(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.border, lineWidth: 1))
        .fixedSize()
    }
}

/// The time axis under Compute's trend and temperature charts. Each time
/// label starts at its tick and runs right, so a tick near the window's
/// newest edge had its label cut at the plot's trailing edge, down to its
/// first character ("2" for "21:16"). A tick that close keeps its grid
/// line and drops its label.
enum ChartTimeAxis {
    /// The share of the window at the trailing edge whose ticks go
    /// unlabelled: about 50pt on the Compute page's chart. The popup's
    /// narrow chart needs a larger share for the same room.
    static func edge(compact: Bool) -> Double { compact ? 0.12 : 0.06 }

    static func labels(_ date: Date?, in domain: ClosedRange<Date>, compact: Bool = false) -> Bool {
        guard let date else { return false }
        let span = domain.upperBound.timeIntervalSince(domain.lowerBound)
        guard span > 0 else { return true }
        return domain.upperBound.timeIntervalSince(date) >= span * edge(compact: compact)
    }
}

/// A chart's hover furniture, kept in its own view so only it redraws
/// on a mouse move: the shared rule at the hovered time on every chart
/// bound to `hovered`, and the readout bubble on the one under the
/// pointer, clamped inside the plot.
struct ChartHoverLayer<Bubble: View>: View {
    let proxy: ChartProxy
    @Binding var hovered: Date?
    @ViewBuilder var bubble: (Date) -> Bubble
    @State private var pointerInside = false
    @State private var bubbleSize: CGSize = .zero

    var body: some View {
        GeometryReader { geometry in
            if let hovered, let plotFrame = proxy.plotFrame,
               let x = proxy.position(forX: hovered) {
                let plot = geometry[plotFrame]
                Rectangle().fill(Theme.border)
                    .frame(width: 1, height: plot.height)
                    .position(x: plot.minX + x, y: plot.midY)
                    .allowsHitTesting(false)
            }
            Rectangle().fill(.clear).contentShape(.rect)
                .onContinuousHover { phase in
                    switch phase {
                    case .active(let point):
                        guard let plot = proxy.plotFrame else { return }
                        pointerInside = true
                        let time = proxy.value(atX: point.x - geometry[plot].origin.x, as: Date.self)
                        if time != hovered { hovered = time }
                    case .ended:
                        pointerInside = false
                        hovered = nil
                    }
                }
            if pointerInside, let hovered, let plotFrame = proxy.plotFrame,
               let x = proxy.position(forX: hovered) {
                bubble(hovered)
                    .fixedSize()
                    .onGeometryChange(for: CGSize.self) { $0.size } action: { bubbleSize = $0 }
                    .position(origin(atX: x, plot: geometry[plotFrame]))
                    .allowsHitTesting(false)
            }
        }
    }

    /// The bubble's center at the hovered x, just under the plot's top
    /// edge, slid sideways until it clears both edges of the plot.
    private func origin(atX x: CGFloat, plot: CGRect) -> CGPoint {
        let half = bubbleSize.width / 2
        let px = plot.origin.x + x
        let lo = plot.minX + half + 4, hi = plot.maxX - half - 4
        let cx = lo <= hi ? min(max(px, lo), hi) : plot.midX
        return CGPoint(x: cx, y: plot.minY + bubbleSize.height / 2 + 4)
    }
}
