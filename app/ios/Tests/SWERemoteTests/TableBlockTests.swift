import XCTest
@testable import SWERemote

final class TableBlockTests: XCTestCase {
    func testTheCutNoteSaysRowAndColumnInTheSingular() {
        XCTAssertEqual(TableBlock.hiddenNote(rows: 1, columns: 0), "1 more row not shown")
        XCTAssertEqual(TableBlock.hiddenNote(rows: 0, columns: 1), "1 more column not shown")
        XCTAssertEqual(TableBlock.hiddenNote(rows: 3, columns: 2), "3 more rows and 2 more columns not shown")
        XCTAssertNil(TableBlock.hiddenNote(rows: 0, columns: 0))
    }
}
