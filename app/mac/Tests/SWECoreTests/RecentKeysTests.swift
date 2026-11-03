import Testing
@testable import SWECore

@Suite struct RecentKeysTests {
    @Test func theOldestKeyFallsOutPastCapacity() {
        var recent = RecentKeys<String>(capacity: 2)
        #expect(recent.touch("a") == [])
        #expect(recent.touch("b") == [])
        // Using "a" again makes "b" the oldest.
        #expect(recent.touch("a") == [])
        #expect(recent.touch("c") == ["b"])
        #expect(recent.keys == ["c", "a"])
    }

    @Test func aRemovedKeyFreesItsPlace() {
        var recent = RecentKeys<String>(capacity: 2)
        recent.touch("a")
        recent.touch("b")
        recent.remove("a")
        #expect(recent.touch("c") == [])
        #expect(recent.keys == ["c", "b"])
    }
}
