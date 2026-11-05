import SWECore
import SwiftUI

/// The Compute header's fan button. The glyph fills while a manual speed
/// is held, so a forgotten override shows from across the page.
struct FanButton: View {
    @Environment(AppModel.self) private var app
    @State private var showing = false

    var body: some View {
        Button("Fans", systemImage: app.fans.isHolding ? "fan.fill" : "fan") { showing = true }
            .iconButton()
            // `.help`, not a tip: a tip's popover would open beside the
            // fan popover this button opens.
            .help(app.fans.isHolding ? "Fans: manual" : "Fans")
            .popover(isPresented: $showing, arrowEdge: .bottom) {
                FanPopover(refreshes: false)
                    .padding(16)
                    .frame(width: 360)
            }
    }
}

/// What the fans are doing, the Mac's temperatures, and a manual speed.
/// Choosing Automatic hands the fans straight back to macOS; a manual
/// speed asks for the password once, then holds while SWE is open.
///
/// One view for both places it opens: the Compute page's header, and
/// (`compact`) the menu bar panel's Compute tab, which folds the page's
/// checkbox and steppers into dropdowns, the captions into info tips
/// and the temperatures into icon chips, and leaves the helper's
/// removal to the page.
struct FanPopover: View {
    @Environment(AppModel.self) private var app
    var compact = false
    /// Whether this popover reads the fans on its own clock. The page's
    /// copy leaves it to the Compute page's refresh loop, which already
    /// reads them while the page shows, so the two never double up.
    var refreshes = true
    /// Shown mode, apart from the phase: picking Manual only opens the
    /// slider, nothing changes until Set speed.
    @State private var manual = false

    private var fans: FanControl { app.fans }

    private var mode: Binding<Bool> {
        Binding(get: { manual },
                set: { wantsManual in
                    manual = wantsManual
                    if !wantsManual && fans.phase != .automatic { fans.restoreAutomatic() }
                })
    }

    private var level: Binding<Double> {
        Binding(get: { fans.level }, set: { fans.level = $0 })
    }

    /// The threshold's slider applies at once; Manual's waits for its button.
    private var thresholdLevel: Binding<Double> {
        Binding(get: { fans.level }, set: { fans.setThresholdLevel($0) })
    }

    /// Switching on may install the helper (the one password prompt), so
    /// it goes through `enableThreshold`; switching off is immediate, and
    /// wins over an enable still waiting on that prompt.
    private var thresholdOn: Binding<Bool> {
        Binding(get: { fans.thresholdEnabled },
                set: { on in
                    if on {
                        Task { await fans.enableThreshold() }
                    } else {
                        fans.thresholdEnabled = false
                    }
                })
    }

    /// The compact form's dropdown doubles as the rule's switch: Off, or
    /// the degree that turns it on (or just moves the line when it is on).
    private var thresholdPick: Binding<Double> {
        Binding(get: { fans.thresholdEnabled ? fans.threshold : 0 },
                set: { pick in
                    if pick == 0 {
                        fans.thresholdEnabled = false
                    } else {
                        fans.threshold = pick
                        // Only an off rule switches on (and may install
                        // the helper); an on one just moves its line.
                        if !fans.thresholdEnabled { thresholdOn.wrappedValue = true }
                    }
                })
    }

    private var threshold: Binding<Double> {
        Binding(get: { fans.threshold }, set: { fans.threshold = $0 })
    }

    private var release: Binding<Double> {
        Binding(get: { fans.releaseBelow }, set: { fans.releaseBelow = $0 })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 8 : 14) {
            HStack {
                Text("Fans").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Spacer()
                Text(status).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if !fans.loaded {
                Text("Reading the fans…").font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else if fans.fans.isEmpty {
                Text("This Mac has no fans SWE can read.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else {
                VStack(spacing: compact ? 6 : 8) {
                    ForEach(fans.fans) { FanRow(fan: $0, compact: compact) }
                }
                temperatures
                Divider().overlay(Theme.border)
                if fans.helperOutdated { FanHelperUpdateNotice() }
                Tabs(options: [(false, "Automatic"), (true, "Manual")], selection: mode,
                     style: .segmented, fillWidth: compact)
                if manual { manualControls } else if compact { compactThresholdControls } else { thresholdControls }
                if case .failed(let message) = fans.phase {
                    Text(message).font(Theme.caption).foregroundStyle(Theme.dangerText)
                }
                if !compact, fans.helperInstalled || fans.helperOutdated {
                    Button("Remove fan helper", role: .destructive) { Task { await fans.removeHelper() } }
                        .plainButton()
                        .font(Theme.caption)
                        .disabled(fans.busy)
                        .hoverTip("Hands the fans back to macOS and uninstalls the helper. Asks for your password.", replacesLabel: false)
                }
            }
        }
        // The threshold's own hold is an automatic behaviour, so it opens
        // on the Automatic side, where its switch lives.
        .onAppear { manual = fans.isHolding && !fans.heldByThreshold }
        .task {
            guard refreshes else { return }
            while !Task.isCancelled {
                await fans.refresh()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    /// The speed slider, shared by Manual and the threshold.
    private func speedSlider(_ title: String, value: Binding<Double>) -> some View {
        VStack(alignment: .leading, spacing: compact ? 6 : 8) {
            HStack {
                Text(title).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                Spacer()
                if let rpm = fans.targetRPM {
                    Text("\(Self.rpm(rpm)) · \(Int((fans.level * 100).rounded()))%")
                        .font(Theme.bodySmall.monospacedDigit()).foregroundStyle(Theme.textSecondary)
                }
            }
            Slider(value: value, in: 0...1)
                .tint(Theme.interactive)
        }
    }

    /// A caption under the controls, or in the compact form an info mark
    /// beside them that shows it on hover.
    @ViewBuilder private func caption(_ text: String) -> some View {
        if compact {
            Image(systemName: "info.circle")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .hoverTip(text)
        } else {
            Text(text)
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var thresholdControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Toggle("Fan on temperature", isOn: thresholdOn)
                    .toggleStyle(.checkbox)
                    .font(Theme.bodySmall)
                    .disabled(fans.busy)
                Spacer()
                Text("\(Int(fans.threshold.rounded())) °C")
                    .font(Theme.bodySmall.monospacedDigit())
                    .foregroundStyle(fans.thresholdEnabled ? Theme.textPrimary : Theme.textSecondary)
                Stepper("Fan on temperature", value: threshold, in: FanControl.thresholdRange, step: 1)
                    .labelsHidden()
            }
            HStack(spacing: 8) {
                Text("Fan off temperature")
                    .font(Theme.bodySmall)
                    .foregroundStyle(fans.thresholdEnabled ? Theme.textPrimary : Theme.textSecondary)
                Spacer()
                Text("\(Int(fans.releaseBelow.rounded())) °C")
                    .font(Theme.bodySmall.monospacedDigit())
                    .foregroundStyle(fans.thresholdEnabled ? Theme.textPrimary : Theme.textSecondary)
                // Its ceiling is a degree under the on line, so stepping
                // can never put the fans down at or over it.
                Stepper("Fan off temperature", value: release,
                        in: FanControl.releaseRange.lowerBound...fans.threshold - 1, step: 1)
                    .labelsHidden()
            }
            if fans.thresholdEnabled {
                speedSlider("Speed above it", value: thresholdLevel)
            }
            caption(thresholdCaption)
        }
    }

    /// The page's checkbox and steppers folded into dropdowns: Off, or
    /// the CPU temperature that holds the fans at the picked speed.
    @ViewBuilder private var compactThresholdControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Fans up above").font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                Spacer()
                SelectMenu(options: [(0.0, "Off")]
                               + Self.thresholdSteps.map { (Double($0), "\($0) °C") },
                           selection: thresholdPick, width: nil)
                    .disabled(fans.busy)
                caption(thresholdCaption)
            }
            if fans.thresholdEnabled {
                HStack {
                    Text("Fan off below").font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    Spacer()
                    SelectMenu(options: Self.releaseSteps(below: fans.threshold)
                                   .map { (Double($0), "\($0) °C") },
                               selection: release, width: nil)
                }
                speedSlider("Speed above it", value: thresholdLevel)
            }
        }
    }

    private var thresholdCaption: String {
        let line = "\(Int(fans.threshold.rounded())) °C"
        let back = "\(Int(fans.releaseBelow.rounded())) °C"
        if !fans.thresholdEnabled {
            return (compact ? "Optional: past the picked temperature, SWE holds the fans at a speed you pick."
                    : "Optional: when the CPU passes \(line), SWE holds the fans at a speed you pick, "
                    + "instead of waiting for macOS.")
                + (fans.helperInstalled ? "" : " Switching it on installs the fan helper, which asks for "
                   + "your password once.")
        }
        return compact
            ? "Holds this speed until the CPU drops to \(back). Works while SWE is open."
            : "When the CPU passes \(line), SWE holds the fans at this speed until it drops to \(back). "
                + "Works while SWE is open."
    }

    /// One degree steps, like the page's stepper, so a temperature set
    /// there always has a row in the dropdown.
    private static let thresholdSteps =
        Array(Int(FanControl.thresholdRange.lowerBound)...Int(FanControl.thresholdRange.upperBound))

    /// The off temperature's steps: every degree under the on line, so
    /// no pick can land the fans' drop-out at or over it.
    private static func releaseSteps(below threshold: Double) -> [Int] {
        Array(Int(FanControl.releaseRange.lowerBound)...Int(threshold) - 1)
    }

    @ViewBuilder private var manualControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            speedSlider("Speed", value: level)
            HStack {
                Button(buttonTitle) { Task { await fans.applyManual() } }
                    .primaryButton()
                    .disabled(fans.phase == .starting || fans.busy)
                Spacer()
                if compact { caption(manualCaption) }
            }
            if !compact { caption(manualCaption) }
        }
    }

    private var manualCaption: String {
        "A manual speed holds while SWE is open. macOS takes the fans back when you pick "
            + "Automatic or quit SWE, and the fans run flat out if the Mac gets seriously hot."
            + (fans.helperInstalled ? ""
               : " The first time, SWE installs a small helper, which asks for your password once.")
    }

    private var buttonTitle: String {
        if fans.busy { return "Installing…" }
        return switch fans.phase {
        case .manual: "Update speed"
        case .starting: "Starting…"
        case .automatic, .failed: "Set speed"
        }
    }

    private var status: String {
        if fans.heldByThreshold { return "Above \(Int(fans.threshold.rounded())) °C" }
        return switch fans.phase {
        case .automatic, .failed: "Automatic"
        case .starting: "Taking over…"
        case .manual: "Manual"
        }
    }

    /// CPU, GPU and battery: a line of names and readings on the page,
    /// icon chips in the compact form (the component's chart colour on
    /// an icon, its full name on hover).
    @ViewBuilder private var temperatures: some View {
        let temps = temperatureReadings
        if !temps.isEmpty {
            if compact {
                HStack(spacing: 10) {
                    ForEach(temps, id: \.0) { component, value in
                        HStack(spacing: 3) {
                            Image(systemName: component.panelIcon)
                                .foregroundStyle(component.colour)
                                .accessibilityHidden(true)
                            Text(TemperatureChart.formatted(value))
                                .monospacedDigit()
                                .foregroundStyle(Theme.textSecondary)
                        }
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("\(component.title) \(TemperatureChart.formatted(value))")
                        .hoverTip(component.title, replacesLabel: false, pinsOnClick: false)
                    }
                    Spacer()
                }
                .font(Theme.caption)
            } else {
                Text(temps.map { "\($0.0.title) \(TemperatureChart.formatted($0.1))" }.joined(separator: " · "))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
    }

    private var temperatureReadings: [(ThermalComponent, Double)] {
        guard let point = app.compute.history.temperatures.last else { return [] }
        return [ThermalComponent.cpu, .gpu, .battery].compactMap { component in
            point[component].map { (component, $0) }
        }
    }

    static func rpm(_ value: Double) -> String {
        "\(Int(value.rounded()).formatted()) rpm"
    }
}

/// An older fan helper is installed: why SWE won't steer it, and the
/// update (one password prompt). Shared with the menu bar's fan panel.
struct FanHelperUpdateNotice: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(FanControl.outdatedNotice)
                .font(Theme.caption).foregroundStyle(Theme.warningText)
                .fixedSize(horizontal: false, vertical: true)
            Button(app.fans.busy ? "Updating…" : "Update fan helper") { Task { await app.fans.updateHelper() } }
                .secondaryButton()
                .disabled(app.fans.busy)
        }
    }
}

/// One fan: its number, its speed, and how far into its range it runs.
/// The compact form folds the bar into the line between name and rpm.
private struct FanRow: View {
    var fan: FanReading
    var compact = false

    private var share: Double {
        fan.maximum > 0 ? min(max(fan.actual / fan.maximum, 0), 1) : 0
    }

    var body: some View {
        if compact {
            HStack(spacing: 8) {
                Text("Fan \(fan.index + 1)").font(Theme.caption).foregroundStyle(Theme.textPrimary)
                bar
                Text(FanPopover.rpm(fan.actual))
                    .font(Theme.caption.monospacedDigit()).foregroundStyle(Theme.textSecondary)
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text("Fan \(fan.index + 1)").font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    Spacer()
                    Text(FanPopover.rpm(fan.actual))
                        .font(Theme.bodySmall.monospacedDigit()).foregroundStyle(Theme.textSecondary)
                }
                bar
            }
        }
    }

    private var bar: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.usageTrack)
                Capsule().fill(Theme.accentFill).frame(width: geometry.size.width * share)
            }
        }
        .frame(height: 4)
        .accessibilityElement()
        .accessibilityValue("\(Int((share * 100).rounded())) percent")
    }
}

private extension ThermalComponent {
    /// The icon that stands in for the name on the compact temperature
    /// chips. Covers every case so a new component can't slip through.
    var panelIcon: String {
        switch self {
        case .cpu: "cpu"
        case .gpu: "rectangle.3.offgrid.fill"
        case .memory: "memorychip"
        case .ssd: "internaldrive"
        case .battery: "battery.100"
        case .airflow: "wind"
        case .display: "display"
        }
    }
}
