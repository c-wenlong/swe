import Testing
@testable import SWEWidgetsUI

/// The row fit against real desktop content heights: the medium widget's
/// content comes out under 132pt once WidgetKit's margins come off, which
/// used to floor its rows to four and leave a gap.
@Suite struct LimitsRowsTests {
    @Test func mediumHeightsGiveFiveRows() {
        #expect(LimitsView.rows(for: 132, compact: false) == 5)
        #expect(LimitsView.rows(for: 128, compact: false) == 5)
    }

    @Test func smallGivesSixCompactRows() {
        #expect(LimitsView.rows(for: 140, compact: true) == 6)
    }

    @Test func largeGivesAtLeastTheSnapshotCap() {
        // The snapshot carries 12 rows; the large widget wants more.
        #expect(LimitsView.rows(for: 350, compact: false) >= 12)
    }

    @Test func fittedRowsSpreadTheLeftover() {
        // 128pt and five rows leaves 28pt over four gaps: 7 each.
        #expect(LimitsView.rowSpacing(for: 128, count: 5, compact: false) == 7)
        // Room to spare tops out at the maximum gap.
        #expect(LimitsView.rowSpacing(for: 200, count: 5, compact: false) == 8)
        #expect(LimitsView.rowSpacing(for: 140, count: 6, compact: true) == 4)
        // One row gets no spacing at all.
        #expect(LimitsView.rowSpacing(for: 128, count: 1, compact: false) == 0)
    }
}

@Suite struct LimitsOverflowTests {
    @Test func aDroppedLimitIsCountedNotHidden() {
        // Six limits, five rows: four limits and "+2 more".
        #expect(LimitsView.shown(total: 6, capacity: 5) == (rows: 4, more: 2))
        #expect(LimitsView.shown(total: 5, capacity: 5) == (rows: 5, more: 0))
        #expect(LimitsView.shown(total: 0, capacity: 5) == (rows: 0, more: 0))
    }

    @Test func theLargeWidgetSpreadsAShortList() {
        // Six rows in 340pt: as wide as two rows apart, not 8pt.
        #expect(LimitsView.spreadSpacing(for: 340, count: 6) == 40)
        #expect(LimitsView.spreadSpacing(for: 140, count: 6) == 4)
        #expect(LimitsView.spreadSpacing(for: 340, count: 1) == 0)
    }
}

@Suite struct LimitLabelTests {
    @Test func theSmallWidgetDropsTheHarnessBeforeAnAccount() {
        #expect(LimitLine.label(title: "Claude Code: Personal", harness: "claude", window: "session", compact: true)
                == "Personal · session")
        #expect(LimitLine.label(title: "Codex", harness: "codex", window: "weekly", compact: true) == "Codex · weekly")
    }

    @Test func anAccountNameWithItsOwnColonKeepsItsWords() {
        // Only the harness's own name goes: these two would both read
        // "Shared" if everything before the first colon were dropped.
        #expect(LimitLine.label(title: "Cursor Work: Shared", harness: "cursor", window: nil, compact: true)
                == "Cursor Work: Shared")
        #expect(LimitLine.label(title: "Cursor: Cursor Home: Shared", harness: "cursor", window: nil, compact: true)
                == "Cursor Home: Shared")
    }

    @Test func largerWidgetsKeepTheWholeTitle() {
        #expect(LimitLine.label(title: "Claude Code: Personal", harness: "claude", window: "session", compact: false)
                == "Claude Code: Personal · session")
        #expect(LimitLine.label(title: "Cursor", harness: "cursor", window: nil, compact: false) == "Cursor")
    }
}
