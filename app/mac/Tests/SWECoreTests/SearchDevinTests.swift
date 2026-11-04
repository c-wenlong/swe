import Foundation
import Testing
@testable import SWECore

/// Devin CLI sessions live inside `sessions.db`: each indexes under a
/// synthetic `<db>#<id>` path whose stat is the session's last activity and
/// node count, read along the main chain on every pass that owes it one:
/// past the mark's watermark when it holds, else whole. swe's
/// `search_containers.py` writes the same rows (`tests/test_search_harnesses.py`
/// pins its half, and both replay `tests/fixtures/search/devin-chain.json`).
@Suite struct SearchDevinTests {
    /// `sessions` and `message_nodes` as Devin CLI declares them, trimmed to
    /// the columns the index and the token scan read.
    private static let schema = """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, working_directory TEXT NOT NULL,
          backend_type TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
          agent_mode TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
          last_activity_at INTEGER NOT NULL, title TEXT, main_chain_id INTEGER,
          hidden INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE message_nodes (row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL, node_id INTEGER NOT NULL, parent_node_id INTEGER,
          chat_message TEXT NOT NULL, created_at INTEGER NOT NULL, metadata TEXT,
          UNIQUE(session_id, node_id));
        """

    private struct Fixture {
        let temp: TempDir
        let ledger: Ledger
        let store: SearchStore
        let source: SQLiteDatabase
        let db: URL
        var dbPath: String { db.resolvingSymlinksInPath().path }
        var roots: LedgerRoots { LedgerRoots(devinDatabases: [db]) }

        init() throws {
            temp = try TempDir()
            db = temp.path("devin/cli/sessions.db")
            try FileManager.default.createDirectory(
                at: db.deletingLastPathComponent(), withIntermediateDirectories: true)
            source = try SQLiteDatabase(url: db)
            try source.execute(SearchDevinTests.schema)
            ledger = try Ledger(directory: temp.path("cache"), pricing: .empty)
            store = try SearchStore(directory: temp.path("data/search"))
        }

        func attach() async { await ledger.setIndexer(SearchIndexer(store: store)) }
        func scan() async throws -> ScanReport { try await ledger.scan(roots: roots) }

        func addSession(_ id: String, title: String?, head: Int64?, created: Int64,
                        activity: Int64, hidden: Bool = false) throws {
            try source.prepare("""
                INSERT INTO sessions(id, working_directory, created_at, last_activity_at,
                                     title, main_chain_id, hidden)
                VALUES (?, '/repo/devin', ?, ?, ?, ?, ?)
                """)
                .bind([.text(id), .int(created), .int(activity),
                       title.map { .text($0) } ?? .null, head.map { .int($0) } ?? .null,
                       .int(hidden ? 1 : 0)])
                .run()
        }

        func addNode(_ session: String, _ node: Int64, parent: Int64?, _ message: String) throws {
            try source.prepare("""
                INSERT INTO message_nodes(session_id, node_id, parent_node_id, chat_message, created_at)
                VALUES (?, ?, ?, ?, 0)
                """)
                .bind([.text(session), .int(node), parent.map { .int($0) } ?? .null, .text(message)])
                .run()
        }

        /// One session: a system row, a prompt, a streamed reply stored
        /// twice (the shorter copy off the main chain), a tool call and its
        /// result, and a closing answer.
        func seed() throws {
            try addSession("brave-otter", title: "Untitled", head: 6,
                           created: 1_791_300_000, activity: 1_791_300_090)
            try addNode("brave-otter", 0, parent: nil,
                        #"{"role":"system","content":"Current workspace directories"}"#)
            try addNode("brave-otter", 1, parent: 0,
                        #"{"role":"user","content":"find the walrus config","metadata":{"created_at":"2026-10-06T10:00:00Z"}}"#)
            try addNode("brave-otter", 2, parent: 1,
                        #"{"role":"assistant","content":"Look","metadata":{"created_at":"2026-10-06T10:00:05Z"}}"#)
            try addNode("brave-otter", 3, parent: 1,
                        #"{"role":"assistant","content":"Looking for it.","tool_calls":[{"id":"c1","name":"exec","arguments":{"command":"grep -r walrus ."}}],"metadata":{"created_at":"2026-10-06T10:00:06Z"}}"#)
            try addNode("brave-otter", 4, parent: 3,
                        #"{"role":"tool","content":"conf/walrus.toml","tool_call_id":"c1","metadata":{"created_at":"2026-10-06T10:00:07Z"}}"#)
            try addNode("brave-otter", 6, parent: 4,
                        #"{"role":"assistant","content":"It is conf/walrus.toml.","metadata":{"created_at":"2026-10-06T10:00:09Z"}}"#)
        }
    }

    @Test func sessionsIndexAlongTheMainChain() async throws {
        let f = try Fixture()
        try f.seed()
        await f.attach()
        let report = try await f.scan()
        #expect(report.filesIndexed == 1)

        let path = f.dbPath + "#brave-otter"
        let row = try #require(try f.store.fileRow(for: path))
        #expect(row.mtimeMs == 1_791_300_090_000)
        #expect(row.sizeBytes == 6)
        #expect(row.byteOffset == 6)
        #expect(row.ino == nil)
        let rowID = try #require(row.sessionRowID)
        let session = try #require(try f.store.sessionRow(rowID))
        #expect(session.fields.agent == "devin")
        #expect(session.fields.sessionID == "brave-otter")
        // "Untitled" is Devin's placeholder: the first prompt stands in.
        #expect(session.fields.title == "find the walrus config")
        #expect(session.fields.cwd == "/repo/devin")
        #expect(session.fields.createdAt == "2026-10-06T15:20:00.000Z")
        #expect(session.fields.updatedAt == "2026-10-06T15:21:30.000Z")
        #expect(session.fields.messageCount == 5)

        let texts = try f.store.db.prepare("""
            SELECT m.role, f.user_text || f.assistant_text || f.tool_text
            FROM messages m JOIN messages_fts f ON f.rowid = m.id ORDER BY m.id
            """)
        var rows: [String] = []
        while try texts.step() { rows.append("\(texts.text(0) ?? ""): \(texts.text(1) ?? "")") }
        // The streamed copy at node 2 is off the chain; the tool result is
        // labelled with the call it answers.
        #expect(rows == [
            "user: find the walrus config",
            "assistant: Looking for it.",
            "tool: exec: grep -r walrus .",
            "tool: exec: conf/walrus.toml",
            "assistant: It is conf/walrus.toml.",
        ])

        // Nothing moved: the next pass reads nothing.
        #expect(try await f.scan().filesIndexed == 0)
    }

    @Test func aGrownSessionIsReadAgainAndAGoneOneRetires() async throws {
        let f = try Fixture()
        try f.seed()
        try f.addSession("quiet-heron", title: "Heron work", head: nil,
                         created: 1_791_300_000, activity: 1_791_300_000)
        try f.addNode("quiet-heron", 0, parent: nil,
                      #"{"role":"user","content":"count the herons"}"#)
        await f.attach()
        #expect(try await f.scan().filesIndexed == 2)

        try f.addNode("brave-otter", 7, parent: 6,
                      #"{"role":"user","content":"now the narwhal"}"#)
        try f.source.execute(
            "UPDATE sessions SET main_chain_id = 7, last_activity_at = 1791300100 WHERE id = 'brave-otter'")
        try f.source.execute("UPDATE sessions SET hidden = 1 WHERE id = 'quiet-heron'")
        #expect(try await f.scan().filesIndexed == 1)

        #expect(try f.store.fileRow(for: f.dbPath + "#quiet-heron") == nil)
        let row = try #require(try f.store.fileRow(for: f.dbPath + "#brave-otter"))
        let rowID = try #require(row.sessionRowID)
        let session = try #require(try f.store.sessionRow(rowID))
        #expect(session.fields.messageCount == 6)
        #expect(try f.store.status().sessionCount == 1)
    }

    private struct ChainFixture: Decodable {
        struct Stage: Decodable {
            struct Files: Decodable, Equatable {
                var byte_offset: Int64
                var mtime_ms: Double
                var size_bytes: Int64
            }
            struct Session: Decodable, Equatable {
                var title: String
                var updated_at: String
                var message_count: Int
            }
            var note: String
            var sql: [String]
            var same_session: Bool
            var files: Files
            var session: Session
            var rows: [String]
            var mark: String?
        }
        var path_suffix: String
        var stages: [Stage]
    }

    /// `tests/fixtures/search/devin-chain.json`, which swe's
    /// `test_the_shared_fixture_grows_the_same_rows_as_the_app` replays
    /// too: both writers leave the same files row, session, rows and mark
    /// after every stage, so either one continues the other's read.
    @Test func sharedFixtureGrowsTheSameRowsAsSwe() async throws {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("tests/fixtures/search/devin-chain.json")
        let fixture = try JSONDecoder().decode(ChainFixture.self, from: Data(contentsOf: url))
        let f = try Fixture()
        await f.attach()
        let path = f.dbPath + fixture.path_suffix
        let ino = try #require(FileInfo(path: f.dbPath)).inode
        var previous: Int64?
        for stage in fixture.stages {
            for sql in stage.sql { try f.source.execute(sql) }
            #expect(try await f.scan().filesIndexed == 1, "\(stage.note)")
            let row = try #require(try f.store.fileRow(for: path))
            #expect(ChainFixture.Stage.Files(
                byte_offset: row.byteOffset, mtime_ms: row.mtimeMs,
                size_bytes: row.sizeBytes ?? -1) == stage.files, "\(stage.note)")
            let rowID = try #require(row.sessionRowID)
            #expect((rowID == previous) == stage.same_session, "\(stage.note)")
            previous = rowID
            let session = try #require(try f.store.sessionRow(rowID)).fields
            #expect(ChainFixture.Stage.Session(
                title: session.title, updated_at: session.updatedAt ?? "",
                message_count: session.messageCount) == stage.session, "\(stage.note)")
            let texts = try f.store.db.prepare("""
                SELECT m.role, f.user_text || f.assistant_text || f.tool_text
                FROM messages m JOIN messages_fts f ON f.rowid = m.id
                WHERE m.session_row_id = ? ORDER BY m.id
                """)
            texts.bind([.int(rowID)])
            var rows: [String] = []
            while try texts.step() { rows.append("\(texts.text(0) ?? ""): \(texts.text(1) ?? "")") }
            #expect(rows == stage.rows, "\(stage.note)")
            let mark = stage.mark.map {
                $0.replacingOccurrences(of: "{ino}", with: "\(ino)")
                    .replacingOccurrences(of: "{session}", with: "\(rowID)")
            }
            #expect(try f.store.meta(SearchDevin.markKey(path)) == mark, "\(stage.note)")
            #expect(try await f.scan().filesIndexed == 0, "\(stage.note)")
        }
        // A retired session takes its mark with it.
        try f.source.execute("UPDATE sessions SET hidden = 1")
        try f.addSession("quiet-heron", title: "Heron work", head: nil,
                         created: 1_791_300_000, activity: 1_791_300_000)
        try f.addNode("quiet-heron", 0, parent: nil, #"{"role":"user","content":"count the herons"}"#)
        _ = try await f.scan()
        #expect(try f.store.meta(SearchDevin.markKey(path)) == nil)
    }

    @Test func aMarkRoundTripsAndAnotherDatabaseFileReadsWhole() throws {
        let f = try Fixture()
        try f.seed()
        let session = try #require(try SearchDevin.sessions(in: f.source).first)
        let mark = try #require(try SearchDevin.read(in: f.source, session: session, ino: 7).mark)
        #expect(mark == SearchDevin.Mark(ino: 7, row: 6, head: 6, nodes: 6,
                                         title: "find the walrus config"))
        #expect(try SearchDevin.read(in: f.source, session: session, mark: mark, ino: 7).appended)
        #expect(try !SearchDevin.read(in: f.source, session: session, mark: mark, ino: 8).appended)
        var stored = mark
        stored.session = 42
        #expect(SearchDevin.Mark(parsing: mark.value(session: 42)) == stored)
        for junk: String? in [nil, "", "1 2 3", "1 2 3 4 x\nt", "1 2 3 4 5 6"] {
            #expect(SearchDevin.Mark(parsing: junk) == nil)
        }
    }

    @Test func aCommitFencesOnTheRowFromBeforeTheRead() async throws {
        let f = try Fixture()
        try f.seed()
        await f.attach()
        _ = try await f.scan()
        // Another writer (swe) committed while this read ran: a commit that
        // started from no row must not replace the newer one.
        let indexer = SearchIndexer(store: f.store)
        let path = f.dbPath + "#brave-otter"
        let fields = SearchSessionFields(
            agent: "devin", sessionID: "brave-otter", filePath: path, title: "stale",
            cwd: nil, cwdKey: nil, createdAt: nil, updatedAt: nil, messageCount: 0)
        #expect(try !indexer.finishSession(
            path: path, stat: SearchFileStat(mtimeMs: 1, sizeBytes: 1), fileRow: nil,
            fields: fields, messages: []))
        let row = try #require(try f.store.fileRow(for: path))
        let rowID = try #require(row.sessionRowID)
        #expect(try f.store.sessionRow(rowID)?.fields.title == "find the walrus config")
    }

    @Test func aRealTitleWinsAndHeadlessSessionsWalkFromTheNewestNode() throws {
        let f = try Fixture()
        try f.addSession("calm-lynx", title: "Lynx deploy", head: nil,
                         created: 1_791_300_000, activity: 0)
        try f.addNode("calm-lynx", 0, parent: nil, #"{"role":"user","content":"ship the lynx"}"#)
        try f.addNode("calm-lynx", 1, parent: 0, #"{"role":"assistant","content":"draft"}"#)
        try f.addNode("calm-lynx", 2, parent: 0, #"{"role":"assistant","content":"shipped"}"#)
        let session = try #require(try SearchDevin.sessions(in: f.source).first)
        #expect(session.stat == SearchFileStat(mtimeMs: 1_791_300_000_000, sizeBytes: 3))
        let read = try SearchDevin.messages(in: f.source, session: session)
        #expect(read.messages.map(\.text) == ["ship the lynx", "shipped"])
        #expect(SearchDevin.title(of: session, promptTitle: read.promptTitle) == "Lynx deploy")
    }
}
