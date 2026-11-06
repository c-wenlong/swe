import Testing
import SWECore
@testable import SWEWidgetsUI

/// The small Compute widget has no room for the medium's column labels,
/// so its figures name CPU and GPU themselves.
@Suite struct ComputeFiguresTests {
    @Test func theSmallRowNamesItsFigures() {
        let row = WidgetSnapshot.ComputeSection.Row(harness: "codex", memoryBytes: 858_300_000,
                                                    cpuPercent: 12, gpuPercent: 4)
        let line = ComputeLineCompact.figures(row)
        #expect(line.contains("  CPU \(ComputeSummary.percent(12))%"))
        #expect(line.hasSuffix("  GPU \(ComputeSummary.percent(4))%"))
        #expect(line.hasPrefix(ComputeSummary.bytes(858_300_000) + "  CPU"))
    }

    @Test func noGPUFigureLeavesTheLabelOut() {
        let row = WidgetSnapshot.ComputeSection.Row(harness: "cursor", memoryBytes: 619_900_000,
                                                    cpuPercent: 3, gpuPercent: nil)
        #expect(!ComputeLineCompact.figures(row).contains("GPU"))
        #expect(ComputeLineCompact.figures(row).hasSuffix("CPU \(ComputeSummary.percent(3))%"))
    }
}
