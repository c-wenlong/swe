import Foundation

/// The drafts list on the AGENTS.md page.
public enum ChapterDrafts {
    /// `dirty` chapter names in the order the compiled file reads them
    /// (`order`), then any the index leaves out, alphabetically.
    public static func ordered(_ dirty: [String], by order: [String]) -> [String] {
        let wanted = Set(dirty)
        var seen = Set<String>()
        var result: [String] = []
        for name in order where wanted.contains(name) && seen.insert(name).inserted {
            result.append(name)
        }
        result += wanted.subtracting(seen).sorted()
        return result
    }

    /// "Drafts (2)" for the menu button.
    public static func title(count: Int) -> String { "Drafts (\(count))" }
}

/// A generated file's size label beside its preview.
public enum CompiledSize {
    /// A file's size as KiB with one decimal ("12.3 KiB"), 1 KiB being
    /// 1024 bytes. Sizes under 1 KiB read as bytes ("812 B").
    public static func label(bytes: Int) -> String {
        if bytes < 1024 { return "\(max(bytes, 0)) B" }
        return "\(kibNumber(bytes: bytes)) KiB"
    }

    /// The bare KiB figure with one decimal ("3.0", "0.4"), 1 KiB being
    /// 1024 bytes, for a column whose header carries the unit. Sizes
    /// under 1 KiB stay in KiB, so a column reads in one unit.
    public static func kibNumber(bytes: Int) -> String {
        let kib = (Double(max(bytes, 0)) / 1024 * 10).rounded() / 10
        return String(format: "%.1f", kib)
    }
}

/// The Compile tab's Include column: each chapter file's size in KiB and
/// the sums folders and the total row show.
public enum IncludeSizes {
    /// The sum of `sizes` over `files` that pass `included`. A file with
    /// no known size counts as 0; an unchecked file never counts.
    public static func bytes(of files: [String], sizes: [String: Int],
                             included: (String) -> Bool) -> Int {
        files.reduce(0) { $0 + (included($1) ? max(sizes[$1] ?? 0, 0) : 0) }
    }

    /// A row's cell: the bare KiB figure, empty when the size is unknown.
    public static func cell(bytes: Int?) -> String {
        bytes.map { CompiledSize.kibNumber(bytes: $0) } ?? ""
    }
}
