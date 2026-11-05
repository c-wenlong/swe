import CoreGraphics
import SWECore
import Testing
@testable import SWEWidgetsUI

/// The heatmap widget's grid fit: any size WidgetKit hands it, however
/// small, gives a drawable cell and a week count without trapping.
@Suite struct HeatmapGridTests {
    @Test func aRoomyWidgetCapsTheCellAndTheWeeks() {
        let grid = HeatmapView.grid(for: CGSize(width: 2000, height: 300))
        #expect(grid.cell == 10)
        #expect(grid.weeks == WidgetSnapshot.heatmapWeeks)
    }

    @Test func theWidthDecidesHowManyWeeksFit() {
        // 10pt cells and 2pt gaps: (118 + 2) / 12 = 10 weeks.
        #expect(HeatmapView.grid(for: CGSize(width: 118, height: 300)).weeks == 10)
    }

    /// Height 20 used to make the cell exactly -gap: a division by zero
    /// and an `Int(.infinity)` trap. Anything under the caption's height
    /// used to give negative frames.
    @Test func tooShortForTheCaptionStillGivesAPointCell() {
        for height: CGFloat in [0, 10, 20, 33, 34] {
            let grid = HeatmapView.grid(for: CGSize(width: 150, height: height))
            #expect(grid.cell >= 1)
            #expect(grid.weeks >= 0 && grid.weeks <= WidgetSnapshot.heatmapWeeks)
        }
    }

    @Test func zeroAndNegativeSizesDoNotTrap() {
        #expect(HeatmapView.grid(for: .zero).weeks == 0)
        #expect(HeatmapView.grid(for: CGSize(width: -50, height: -50)).weeks == 0)
    }
}
