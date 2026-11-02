import Foundation

/// Block-level Markdown for agent messages: the subset agents actually
/// write (headings, lists, tables, quotes, rules, fenced code). Inline
/// spans (bold, italics, code, links) are left in the text for
/// `AttributedString(markdown:)` to style.
///
/// This is the same parser as the phone's `SWERemote/Hub/Markdown.swift`
/// and the web app's `md()` in `pages/chat.js`; the three stay in step.
/// Where the phone emits flat list items and quote text, the blocks here
/// group into `MarkdownBlock`'s `.list` and `.quote` so the app's
/// `MarkdownBlocks` renders them — same trees, Mac shapes.
public enum ChatMarkdown {

    /// A task item's box, read off the start of a list item's text.
    enum TaskBox: Equatable, Sendable { case open, done }

    static func taskBox(_ text: String) -> (TaskBox, String)? {
        let lower = text.lowercased()
        if lower.hasPrefix("[ ] ") { return (.open, String(text.dropFirst(4))) }
        if lower.hasPrefix("[x] ") { return (.done, String(text.dropFirst(4))) }
        return nil
    }

    public static func blocks(_ text: String) -> [MarkdownBlock] {
        var lines = text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        var out: [MarkdownBlock] = []
        var paragraph: [String] = []
        var items: [MarkdownListItem]?
        var i = 0

        func flushParagraph() {
            let joined = paragraph.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { out.append(.paragraph(joined)) }
            paragraph = []
        }
        func flushList() {
            if let l = items, !l.isEmpty { out.append(.list(l)) }
            items = nil
        }
        func flush() {
            flushParagraph()
            flushList()
        }

        while i < lines.count {
            let line = lines[i]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // Fenced code: everything up to the closing fence, verbatim.
            if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") {
                flush()
                let fence = String(trimmed.prefix(3))
                let lang = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                // A fence indented under a list item: its lines lose that indent.
                let indent = line.prefix { $0 == " " }.count
                var body: [String] = []
                i += 1
                while i < lines.count, !fenceClose(lines[i], marker: fence) {
                    let raw = lines[i]
                    let lead = min(indent, raw.prefix { $0 == " " }.count)
                    body.append(String(raw.dropFirst(lead)))
                    i += 1
                }
                i += 1 // the closing fence (or past the end)
                out.append(.code(body.joined(separator: "\n"), lang.isEmpty ? nil : lang))
                continue
            }

            if trimmed.isEmpty {
                flush()
                i += 1
                continue
            }

            if trimmed.hasPrefix("<json-render>") {
                flush()
                var body = trimmed
                while !body.contains("</json-render>"), i + 1 < lines.count {
                    i += 1
                    body += "\n" + lines[i]
                }
                // Text after the closing tag on its line parses as usual.
                if let close = body.range(of: "</json-render>") {
                    let rest = body[close.upperBound...].trimmingCharacters(in: .whitespaces)
                    body = String(body[..<close.upperBound])
                    if !rest.isEmpty {
                        lines[i] = rest
                        out.append(.rendered(body))
                        continue
                    }
                }
                i += 1
                out.append(.rendered(body))
                continue
            }

            if let (alt, source) = imageLine(trimmed) {
                flush()
                out.append(.image(alt: alt, source: source))
                i += 1
                continue
            }

            // A table: a row of cells, then a |---|:--:| separator row.
            if trimmed.contains("|"), i + 1 < lines.count, let align = separatorAlignments(lines[i + 1]) {
                flush()
                let header = cells(trimmed)
                var rows: [[String]] = []
                i += 2
                while i < lines.count {
                    let row = lines[i].trimmingCharacters(in: .whitespaces)
                    // A line that opens its own block — `> Warning: a | b`
                    // is a quote, ```a|b``` a fence — is never a row.
                    guard !row.isEmpty, row.contains("|"),
                          !startsBlock(lines, at: i, orTable: false) else { break }
                    rows.append(cells(row))
                    i += 1
                }
                out.append(.table(MarkdownTable(header: header, align: align, rows: rows)))
                continue
            }

            if let (level, text) = heading(trimmed) {
                flush()
                out.append(.heading(level, text))
                i += 1
                continue
            }

            if trimmed.allSatisfy({ $0 == "=" }), !paragraph.isEmpty {
                // Setext h1: `===` directly under a paragraph.
                out.append(.heading(1, paragraph.joined(separator: " ")))
                paragraph = []
                i += 1
                continue
            }

            if isRule(trimmed) {
                // A dash-only line under paragraph text is a setext
                // h2, not a rule.
                if !paragraph.isEmpty, trimmed.contains("-"),
                   !trimmed.contains("*"), !trimmed.contains("_") {
                    out.append(.heading(2, paragraph.joined(separator: " ")))
                    paragraph = []
                } else {
                    flush()
                    out.append(.rule)
                }
                i += 1
                continue
            }

            if trimmed.hasPrefix(">") {
                flush()
                var quoted: [String] = []
                while i < lines.count {
                    let q = lines[i].trimmingCharacters(in: .whitespaces)
                    guard q.hasPrefix(">") else { break }
                    var rest = q.dropFirst()
                    if rest.first == " " { rest = rest.dropFirst() }
                    quoted.append(String(rest))
                    i += 1
                }
                out.append(.quote(blocks(quoted.joined(separator: "\n"))))
                continue
            }

            if let item = listItem(line) {
                flushParagraph()
                var text = item.text
                i += 1
                // Indented lines that aren't a new item continue this one,
                // unless they open a block of their own (a fence, a table,
                // a quote, a heading), which then parses on its own.
                while i < lines.count {
                    let next = lines[i]
                    let nt = next.trimmingCharacters(in: .whitespaces)
                    guard !nt.isEmpty, listItem(next) == nil, next.hasPrefix("  ") || next.hasPrefix("\t") else { break }
                    if startsBlock(lines, at: i) { break }
                    text += "\n" + nt
                    i += 1
                }
                var kind: MarkdownListItem.Kind = item.marker == "•"
                    ? .bullet
                    : .ordered(Int(item.marker.dropLast()) ?? 1)
                if let (box, rest) = taskBox(text) {
                    kind = .task(box == .done)
                    text = rest
                }
                if items == nil { items = [] }
                items!.append(MarkdownListItem(depth: item.indent, kind: kind, text: text))
                continue
            }

            flushList()
            paragraph.append(line)
            i += 1
        }
        flush()
        return out
    }

    // MARK: Pieces

    /// `![alt](source)` alone on a line.
    static func imageLine(_ line: String) -> (String, String)? {
        guard line.hasPrefix("!["), line.hasSuffix(")"),
              let close = line.range(of: "]("), line.firstIndex(of: "]") == close.lowerBound
        else { return nil }
        let alt = String(line[line.index(line.startIndex, offsetBy: 2)..<close.lowerBound])
        let source = String(line[close.upperBound..<line.index(before: line.endIndex)])
        // Exactly one image: `![a](x)![b](y)` stays for inline rendering.
        guard !source.contains(" "), !source.contains(")"), !source.contains("![") else { return nil }
        return (alt, source)
    }

    /// Inline `![alt](source)` inside a sentence, as text the app can show.
    /// Code spans and an escaped `\![` keep what the agent wrote.
    public static func inlineImagesAsText(_ text: String) -> String {
        guard text.contains("!["), let re = inlineImageRE else { return text }
        let ns = text as NSString
        var out = ""
        var at = 0
        for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            guard m.range(at: 2).location != NSNotFound else { continue } // a code span
            out += ns.substring(with: NSRange(location: at, length: m.range.location - at))
            out += "*Image: \(ns.substring(with: m.range(at: 2)))*"
            at = m.range.location + m.range.length
        }
        return out + ns.substring(from: at)
    }

    /// A code span (left as is), or an unescaped image (group 2 its alt).
    private static let inlineImageRE = try? NSRegularExpression(
        pattern: #"(`+)[\s\S]*?\1|(?<!\\)!\[([^\]]*)\]\([^)\s]+\)"#)

    /// A line that closes the open fence: the marker alone, or a longer
    /// run of the same character — ``` ` ```rust` ``` is content, not a
    /// close. (CommonMark agrees; the phone's prefix match drops it.)
    static func fenceClose(_ line: String, marker: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        return t.hasPrefix(marker) && t.allSatisfy { $0 == marker.first }
    }

    /// Whether `lines[i]` opens a block that isn't list-item text.
    /// `orTable: false` inside a table's row loop: a data row followed
    /// by a separator-looking line is still a row, not a second table.
    static func startsBlock(_ lines: [String], at i: Int, orTable: Bool = true) -> Bool {
        let t = lines[i].trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("```") || t.hasPrefix("~~~") || t.hasPrefix(">") || heading(t) != nil { return true }
        if t.hasPrefix("<json-render>") || imageLine(t) != nil { return true }
        if orTable, t.contains("|"), i + 1 < lines.count, separatorAlignments(lines[i + 1]) != nil { return true }
        return false
    }

    static func heading(_ line: String) -> (Int, String)? {
        let hashes = line.prefix { $0 == "#" }
        guard (1...6).contains(hashes.count) else { return nil }
        let rest = line.dropFirst(hashes.count)
        guard rest.first == " " else { return nil }
        var text = rest.trimmingCharacters(in: .whitespaces)
        // An optional closing run ("## Title ##") goes only when a space
        // sets it apart, so "C#" and "#123" keep their hashes.
        if let spaceRun = text.range(of: #"\s+#+$"#, options: .regularExpression) {
            text.removeSubrange(spaceRun)
        } else if text.allSatisfy({ $0 == "#" }) {
            text = ""
        }
        return (hashes.count, text)
    }

    static func isRule(_ line: String) -> Bool {
        let compact = line.replacingOccurrences(of: " ", with: "")
        guard compact.count >= 3, let first = compact.first, "-*_".contains(first) else { return false }
        return compact.allSatisfy { $0 == first }
    }

    static func listItem(_ line: String) -> (indent: Int, marker: String, text: String)? {
        let leading = line.prefix { $0 == " " || $0 == "\t" }
        let width = leading.reduce(0) { $0 + ($1 == "\t" ? 4 : 1) }
        let rest = line.dropFirst(leading.count)
        if let first = rest.first, "-*+".contains(first), rest.dropFirst().first == " " {
            // "- [ ] task" and "- [x] task" keep their box as text.
            return (width / 2, "•", String(rest.dropFirst(2)))
        }
        let digits = rest.prefix { $0.isNumber }
        guard !digits.isEmpty, digits.count <= 3 else { return nil }
        let after = rest.dropFirst(digits.count)
        guard let punct = after.first, punct == "." || punct == ")", after.dropFirst().first == " " else { return nil }
        return (width / 2, "\(digits)\(punct)", String(after.dropFirst(2)))
    }

    /// Cells of a `| a | b |` row. Only `\|` is an escape here: it stays
    /// in its cell as `|`. Every other backslash (`C:\Users`, `\d+`) is
    /// left for the inline parser.
    static func cells(_ row: String) -> [String] {
        var s = row.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("|") { s.removeFirst() }
        if s.hasSuffix("|") && !s.hasSuffix("\\|") { s.removeLast() }
        var out: [String] = []
        var current = ""
        var chars = Array(s)[...]
        while let ch = chars.popFirst() {
            if ch == "\\", chars.first == "|" {
                current.append("|")
                chars.removeFirst()
            } else if ch == "|" {
                out.append(current.trimmingCharacters(in: .whitespaces))
                current = ""
            } else {
                current.append(ch)
            }
        }
        out.append(current.trimmingCharacters(in: .whitespaces))
        return out
    }

    /// The alignments a `|---|:--:|--:|` row sets, or nil when the line
    /// isn't a separator row.
    static func separatorAlignments(_ line: String) -> [MarkdownTable.Align]? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.contains("-") else { return nil }
        let parts = cells(trimmed)
        guard !parts.isEmpty else { return nil }
        var out: [MarkdownTable.Align] = []
        for p in parts {
            let core = p.trimmingCharacters(in: CharacterSet(charactersIn: ":"))
            guard !core.isEmpty, core.allSatisfy({ $0 == "-" }) else { return nil }
            let left = p.hasPrefix(":"), right = p.hasSuffix(":")
            out.append(left && right ? .center : right ? .trailing : .leading)
        }
        // A single dashed "cell" without pipes is a rule, not a table.
        if out.count == 1 && !trimmed.contains("|") { return nil }
        return out
    }
}

extension ChatMarkdown {
    /// One span of agent text → styled runs, the phone's `RichText.inline`.
    /// An `![alt](src)` inside a sentence folds to "*Image: alt*"; a link
    /// that can't open in a browser — the agent's own files, like the
    /// references Codex appends at the end of a turn — is no link at all:
    /// its label shows as a code-styled file name instead of a dead link.
    public static func inline(_ md: String) -> AttributedString {
        let source = inlineImagesAsText(md)
        var out = (try? AttributedString(
            markdown: source,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(source)
        for run in out.runs {
            guard let url = run.link, !url.isWebURL else { continue }
            out[run.range].link = nil
            out[run.range].inlinePresentationIntent = .code
        }
        return out
    }
}
