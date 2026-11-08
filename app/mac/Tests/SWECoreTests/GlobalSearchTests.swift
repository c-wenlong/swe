import Testing
@testable import SWECore

@Suite struct GlobalSearchTests {
    private func item(_ kind: GlobalSearchItem.Kind = .page, title: String,
                      detail: String? = nil, keywords: [String] = []) -> GlobalSearchItem {
        GlobalSearchItem(kind: kind, title: title, detail: detail,
                         keywords: keywords, target: .page(.overview))
    }

    @Test func emptyNeedleMatchesNothing() {
        let items = [item(title: "Sessions"), item(title: "Settings")]
        #expect(GlobalSearch.matches("", items: items).isEmpty)
        #expect(GlobalSearch.matches("   ", items: items).isEmpty)
    }

    @Test func browseListsOnlyNavigation() {
        let items = [item(.page, title: "Sessions"),
                     item(.tab, title: "Explore"),
                     item(.session, title: "Fix the build"),
                     item(.harness, title: "Codex")]
        #expect(GlobalSearch.browse(items).map(\.title) == ["Sessions", "Explore"])
    }

    @Test func everyTokenMustMatch() {
        let items = [item(title: "Session search"),
                     item(title: "Sessions"),
                     item(title: "Search index")]
        let hits = GlobalSearch.matches("session search", items: items)
        #expect(hits.map(\.title) == ["Session search"])
    }

    @Test func titlePrefixBeatsTitleSubstringBeatsDetailBeatsKeyword() {
        let items = [item(title: "Calendar", keywords: ["session"]),
                     item(title: "Overview", detail: "session stats"),
                     item(title: "The session list"),
                     item(title: "Sessions")]
        let hits = GlobalSearch.matches("session", items: items)
        #expect(hits.map(\.title) == ["Sessions", "The session list", "Overview",
                                     "Calendar"])
    }

    @Test func tiedPrefixesFallBackToKindThenTitle() {
        // "session" prefixes both titles, so the same kind's rows order
        // alphabetically.
        let items = [item(.session, title: "session zulu"),
                     item(.session, title: "session alpha"),
                     item(.page, title: "session mid")]
        #expect(GlobalSearch.matches("session", items: items).map(\.title)
            == ["session mid", "session alpha", "session zulu"])
    }

    @Test func matchingIsCaseInsensitive() {
        #expect(GlobalSearch.matches("SESSIONS", items: [item(title: "Sessions")]).count == 1)
        #expect(GlobalSearch.matches("mcp", items: [item(title: "MCP servers")]).count == 1)
    }

    @Test func keywordsMatch() {
        let items = [item(title: "Claude", keywords: ["claude-desktop"]),
                     item(title: "Codex")]
        #expect(GlobalSearch.matches("desktop", items: items).map(\.title) == ["Claude"])
    }

    @Test func equalScoresSortByKindThenTitle() {
        // "a" prefixes each title: pages, then tabs, then sessions and
        // the rest in kind order.
        let hits = GlobalSearch.matches("a", items: [
            item(.session, title: "a one"),
            item(.page, title: "a two"),
            item(.harness, title: "a three"),
        ])
        #expect(hits.map(\.kind) == [.page, .session, .harness])
    }

    @Test func limitCapsTheRows() {
        let items = (0..<60).map { item(title: "Session \($0)") }
        #expect(GlobalSearch.matches("session", items: items).count == 50)
        #expect(GlobalSearch.matches("session", items: items, limit: 10).count == 10)
    }

    @Test func detailAndKeywordTokensCanMix() {
        let items = [item(title: "Sessions", detail: "Usage"),
                     item(title: "Pricing", keywords: ["usage"])]
        #expect(GlobalSearch.matches("sessions usage", items: items).map(\.title) == ["Sessions"])
        #expect(GlobalSearch.matches("pricing usage", items: items).map(\.title) == ["Pricing"])
    }
}
