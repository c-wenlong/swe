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
        let kib = (Double(bytes) / 1024 * 10).rounded() / 10
        return String(format: "%.1f KiB", kib)
    }
}
