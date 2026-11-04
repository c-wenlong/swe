import Foundation
import SQLite3

/// Devin CLI sessions for the search index. Every turn lives in
/// `~/.local/share/devin/cli/sessions.db` (`message_nodes`), so each session
/// indexes under a synthetic `"<dbPath>#<sessionId>"` path the way
/// `SearchOpenCode` does, read whenever its stat moves: only the nodes past
/// the `rowid` watermark its `Mark` records, or the whole chain when the
/// mark no longer holds. The
/// `transcripts/*.json` ATIF exports beside the database cover only the
/// sessions someone exported; both are read, and search keeps one hit per
/// session id.
///
/// swe writes the same rows (`sessions/search_containers.py`): keep the
/// stat, the chain walk, the mark and the fields in step, or each writer
/// reads the other's rows as stale and replaces them on every pass.
enum SearchDevin {
    /// One visible `sessions` row.
    struct Session {
        var id: String
        var title: String
        var directory: String?
        var createdMs: Int64
        var activityMs: Int64
        /// `main_chain_id`: the head of the chain that holds every message's
        /// final version.
        var head: Int64?
        /// Every node the session holds, streamed copies included: a
        /// streamed reply is stored again as it grows, so the count moves
        /// with it. Together with the activity time it is the whole change
        /// detector.
        var nodes: Int64
        /// The newest `rowid` among them, read in the same statement as
        /// `nodes`: the watermark a read records. 0 for an empty session.
        var maxRow: Int64 = 0

        var lastActivityMs: Int64 { activityMs > 0 ? activityMs : createdMs }

        /// The stat `readNeed` compares; dev/ino stay nil — a container row
        /// proves nothing about them.
        var stat: SearchFileStat {
            SearchFileStat(mtimeMs: Double(lastActivityMs), sizeBytes: nodes)
        }
    }

    /// Devin stamps are epoch seconds; a value past `1e12` is already
    /// milliseconds (`SearchStamp`'s rule).
    static func milliseconds(_ value: Int64) -> Int64 {
        guard value > 0 else { return 0 }
        return value > 1_000_000_000_000 ? value : value * 1000
    }

    /// Every session the database shows, newest activity first. `limit`
    /// keeps only the newest N, and a capped result is not a census.
    static func sessions(in db: SQLiteDatabase, limit: Int? = nil) throws -> [Session] {
        let sessionColumns = try columns(of: "sessions", in: db)
        guard sessionColumns.isSuperset(of: ["id", "created_at", "last_activity_at"]),
              try columns(of: "message_nodes", in: db)
                .isSuperset(of: ["session_id", "node_id", "parent_node_id", "chat_message"])
        else {
            throw SQLiteDatabase.Failure(
                code: SQLITE_ERROR,
                message: "lacks the sessions/message_nodes schema this build reads")
        }
        let column = { (name: String) in sessionColumns.contains(name) ? "s.\(name)" : "NULL" }
        let hidden = sessionColumns.contains("hidden") ? " WHERE s.hidden = 0" : ""
        let statement = try db.prepare("""
            SELECT s.id, \(column("title")), \(column("working_directory")),
                   s.created_at, s.last_activity_at, \(column("main_chain_id")),
                   (SELECT COUNT(*) FROM message_nodes n WHERE n.session_id = s.id),
                   (SELECT MAX(n.rowid) FROM message_nodes n WHERE n.session_id = s.id)
            FROM sessions s\(hidden)
            ORDER BY s.last_activity_at DESC, s.id
            \(limit.map { "LIMIT \($0)" } ?? "")
            """)
        var sessions: [Session] = []
        while try statement.step() {
            guard let id = statement.text(0), !id.isEmpty else { continue }
            sessions.append(Session(
                id: id, title: statement.text(1) ?? "", directory: statement.text(2),
                createdMs: milliseconds(statement.int(3)),
                activityMs: milliseconds(statement.int(4)),
                head: statement.isNull(5) ? nil : statement.int(5),
                nodes: statement.int(6), maxRow: statement.int(7)))
        }
        return sessions
    }

    /// The `meta` key holding a session's `Mark`.
    static func markKey(_ path: String) -> String { "devin_chain:" + path }

    /// Where the last read of one session stopped, kept in the index's
    /// `meta` table beside the `files` row it was committed with
    /// (`DevinMark` in `search_containers.py`).
    ///
    /// Devin writes nodes with `INSERT OR REPLACE` into an `AUTOINCREMENT`
    /// table, so a new or rewritten node always takes a `rowid` above
    /// `row`, and a rewrite also removes a row at or below it. A mark holds
    /// while the session still has exactly `nodes` rows at or below `row`
    /// and its chain, walked down from the current head, first meets such
    /// a row at `head`: everything past it is new. `ino` is the database
    /// file's, so a replaced database never continues an old mark.
    /// `session` is the index session the rows went to; `title` the
    /// first-prompt title the chain offered, if any.
    struct Mark: Equatable {
        var ino: UInt64
        var row: Int64
        var head: Int64
        var nodes: Int64
        var session: Int64 = 0
        var title: String?

        /// The stored form, the same bytes swe writes.
        func value(session: Int64) -> String {
            "\(ino) \(row) \(head) \(nodes) \(session)\n\(title ?? "")"
        }

        init(ino: UInt64, row: Int64, head: Int64, nodes: Int64,
             session: Int64 = 0, title: String? = nil) {
            self.ino = ino
            self.row = row
            self.head = head
            self.nodes = nodes
            self.session = session
            self.title = title
        }

        init?(parsing value: String?) {
            guard let value else { return nil }
            let numbers: Substring
            let title: Substring
            if let newline = value.firstIndex(of: "\n") {
                numbers = value[..<newline]
                title = value[value.index(after: newline)...]
            } else {
                numbers = value[...]
                title = ""
            }
            let parts = numbers.split(separator: " ", omittingEmptySubsequences: false)
            guard parts.count == 5, let ino = UInt64(parts[0]), let row = Int64(parts[1]),
                  let head = Int64(parts[2]), let nodes = Int64(parts[3]),
                  let session = Int64(parts[4])
            else { return nil }
            self.init(ino: ino, row: row, head: head, nodes: nodes, session: session,
                      title: title.isEmpty ? nil : String(title))
        }
    }

    /// One session's read: every message along the main chain, or with
    /// `appended` only those past the mark's head. `mark` is what the
    /// commit records for the next read (nil for an empty chain).
    struct Read {
        var messages: [SearchMessage]
        var promptTitle: String?
        var appended: Bool
        var mark: Mark?
    }

    private struct Node {
        var id: Int64
        var row: Int64
        var parent: Int64?
        var raw: String?
    }

    /// One node through the `UNIQUE(session_id, node_id)` index. Loading
    /// every node's parent to walk a 50,000-node forest took 1 to 3 s for
    /// a 260-node chain (#1613).
    private static func node(
        _ statement: Statement, session: Session, id: Int64
    ) throws -> Node? {
        statement.bind([.text(session.id), .int(id)])
        guard try statement.step() else { return nil }
        return Node(id: id, row: statement.int(0),
                    parent: statement.isNull(1) ? nil : statement.int(1),
                    raw: statement.text(2))
    }

    private static func nodeStatement(_ db: SQLiteDatabase) throws -> Statement {
        try db.prepare("""
            SELECT rowid, parent_node_id, chat_message FROM message_nodes
            WHERE session_id = ? AND node_id = ?
            """)
    }

    /// `main_chain_id` when that node exists, else the newest node.
    private static func head(in db: SQLiteDatabase, session: Session) throws -> Int64? {
        if let head = session.head {
            let exists = try db.prepare(
                "SELECT 1 FROM message_nodes WHERE session_id = ? AND node_id = ?")
            exists.bind([.text(session.id), .int(head)])
            if try exists.step() { return head }
        }
        let newest = try db.prepare("SELECT MAX(node_id) FROM message_nodes WHERE session_id = ?")
        newest.bind([.text(session.id)])
        guard try newest.step(), !newest.isNull(0) else { return nil }
        return newest.int(0)
    }

    /// The chain from `start` down, newest first. Streamed copies are
    /// siblings in a forest; walking parents from the head gives one node
    /// per message. With `floor` the walk stops at the first node at or
    /// below that `rowid` and returns it as `stop`.
    private static func walk(
        _ statement: Statement, session: Session, from start: Int64?, floor: Int64? = nil
    ) throws -> (nodes: [Node], stop: Node?) {
        var nodes: [Node] = []
        var seen: Set<Int64> = []
        var cursor = start
        while let id = cursor, !seen.contains(id) {
            guard let found = try node(statement, session: session, id: id) else { break }
            if let floor, found.row <= floor { return (nodes, found) }
            seen.insert(id)
            nodes.append(found)
            cursor = found.parent
        }
        return (nodes, nil)
    }

    /// The `tool_call_id → name` pairs one record adds to the decoder.
    private static func callNames(_ raw: String?) -> [String: String] {
        guard let raw else { return [:] }
        var scratch = SearchDecoder(.devin)
        _ = scratch.decode(line: Data(raw.utf8))
        return scratch.devinCallNames
    }

    /// The call a `role: "tool"` record answers.
    private static func toolCallID(_ raw: String?) -> String? {
        guard let raw,
              let record = (try? JSONSerialization.jsonObject(with: Data(raw.utf8))) as? [String: Any],
              record["role"] as? String == "tool"
        else { return nil }
        return JSONValue.string(record["tool_call_id"])
    }

    /// The call names the appended `span` looks up but never assigns, from
    /// the indexed chain below it: the nearest assignment walking down,
    /// which is the one a whole read's decoder would hold. Usually the
    /// node just below the span.
    private static func primeCalls(
        _ statement: Statement, session: Session, span: [Node], stop: Node
    ) throws -> [String: String] {
        var assigned: Set<String> = []
        var needed: Set<String> = []
        for node in span {
            if let id = toolCallID(node.raw), !assigned.contains(id) { needed.insert(id) }
            assigned.formUnion(callNames(node.raw).keys)
        }
        var resolved: [String: String] = [:]
        guard !needed.isEmpty else { return resolved }
        var current = stop
        var seen: Set<Int64> = [stop.id]
        while true {
            for (id, name) in callNames(current.raw) where needed.contains(id) && resolved[id] == nil {
                resolved[id] = name
            }
            guard resolved.count < needed.count, let parent = current.parent,
                  seen.insert(parent).inserted,
                  let next = try node(statement, session: session, id: parent)
            else { return resolved }
            current = next
        }
    }

    private static func decode(
        _ nodes: [Node], with decoder: inout SearchDecoder
    ) -> (messages: [SearchMessage], title: String?) {
        var messages: [SearchMessage] = []
        var title: String?
        for node in nodes {
            guard let raw = node.raw else { continue }
            let output = decoder.decode(line: Data(raw.utf8))
            messages += output.messages
            if title == nil { title = output.hints.title }
        }
        return (messages, title)
    }

    /// The session's messages through the Devin decoder (its `chat_message`
    /// shape). With a `mark` the last commit recorded, only the chain past
    /// its head is read, unless the session lost a row at or below its
    /// watermark, the database is another file, or the chain no longer runs
    /// through its head (`main_chain_id` moved to a branch, a streamed copy
    /// replaced the head): then the whole chain is read again. Python's
    /// `devin_read`.
    static func read(
        in db: SQLiteDatabase, session: Session, mark: Mark? = nil, ino: UInt64 = 0
    ) throws -> Read {
        let statement = try nodeStatement(db)
        let start = try head(in: db, session: session)
        if let mark, mark.ino == ino, session.maxRow >= mark.row {
            let below = try db.prepare(
                "SELECT COUNT(*) FROM message_nodes WHERE session_id = ? AND rowid <= ?")
            below.bind([.text(session.id), .int(mark.row)])
            if try below.step(), below.int(0) == mark.nodes {
                let (span, stop) = try walk(statement, session: session, from: start, floor: mark.row)
                if let stop, stop.id == mark.head {
                    let nodes = Array(span.reversed())
                    var decoder = SearchDecoder(.devin)
                    decoder.devinCallNames = try primeCalls(
                        statement, session: session, span: nodes, stop: stop)
                    // A title the chain already offered stays; only a chain
                    // that had none takes the span's first prompt.
                    if mark.title != nil { decoder.primeForAppend() }
                    let decoded = decode(nodes, with: &decoder)
                    let title = mark.title ?? decoded.title
                    return Read(messages: decoded.messages, promptTitle: title, appended: true,
                                mark: Mark(ino: ino, row: session.maxRow, head: start ?? mark.head,
                                           nodes: session.nodes, title: title))
                }
            }
        }
        let chain = try walk(statement, session: session, from: start).nodes
        var decoder = SearchDecoder(.devin)
        let decoded = decode(chain.reversed(), with: &decoder)
        let next = start.flatMap { head in
            chain.isEmpty ? nil : Mark(ino: ino, row: session.maxRow, head: head,
                                       nodes: session.nodes, title: decoded.title)
        }
        return Read(messages: decoded.messages, promptTitle: decoded.title, appended: false, mark: next)
    }

    /// The session's whole chain through the Devin decoder, plus the
    /// first-prompt title candidate the decoder offered.
    static func messages(
        in db: SQLiteDatabase, session: Session
    ) throws -> (messages: [SearchMessage], promptTitle: String?) {
        let read = try read(in: db, session: session)
        return (read.messages, read.promptTitle)
    }

    /// `sessions.title` unless Devin left its placeholder, else the first
    /// prompt.
    static func title(of session: Session, promptTitle: String?) -> String {
        if let title = JSONValue.string(session.title), title != "Untitled" { return title }
        return promptTitle ?? ""
    }

    private static func columns(of table: String, in db: SQLiteDatabase) throws -> Set<String> {
        let statement = try db.prepare("PRAGMA table_info(\(table))")
        var names: Set<String> = []
        while try statement.step() {
            if let name = statement.text(1) { names.insert(name) }
        }
        return names
    }
}
