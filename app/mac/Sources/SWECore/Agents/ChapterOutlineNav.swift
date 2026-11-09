import Foundation

/// Search and keyboard rules for the Edit tab's chapter outline, pure so
/// they stay testable: the filter that prunes the tree and the arrow-key
/// walk over the rows the outline shows.
public enum ChapterOutline {
    // MARK: - Search

    /// `nodes` pruned to the files whose name or AGENTS_MD-relative path
    /// contains `query` (case-insensitive, surrounding spaces ignored).
    /// A matching file keeps every parent folder; a folder whose own name
    /// or path matches stays even when empty. A blank query returns the
    /// tree unchanged. Folder counts follow what is left.
    public static func filter(_ nodes: [ChapterNode], query: String) -> [ChapterNode] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty else { return nodes }
        return nodes.compactMap { prune($0, needle) }
    }

    private static func matches(_ node: ChapterNode, _ needle: String) -> Bool {
        node.name.localizedCaseInsensitiveContains(needle)
            || node.path.localizedCaseInsensitiveContains(needle)
    }

    private static func prune(_ node: ChapterNode, _ needle: String) -> ChapterNode? {
        guard node.isFolder else { return matches(node, needle) ? node : nil }
        let kept = node.children.compactMap { prune($0, needle) }
        guard !kept.isEmpty || matches(node, needle) else { return nil }
        let count = kept.reduce(0) { $0 + ($1.isFolder ? $1.fileCount : 1) }
        return ChapterNode(name: node.name, path: node.path, isFolder: true,
                           children: kept, fileCount: count)
    }

    // MARK: - Keyboard

    /// One row the outline shows, top to bottom.
    public struct Row: Equatable, Sendable {
        public let path: String
        public let isFolder: Bool
        /// The enclosing folder's path; "" at the top level.
        public let parent: String

        public init(path: String, isFolder: Bool, parent: String) {
            self.path = path
            self.isFolder = isFolder
            self.parent = parent
        }
    }

    public enum Key: Sendable { case up, down, left, right }

    /// What an arrow key asks the outline to do.
    public enum Step: Equatable, Sendable {
        case focus(String)
        case collapse(String)
        case expand(String)
        case none
    }

    /// The rows on screen: every node, with a folder's children only
    /// while `isExpanded` says it is open.
    public static func visibleRows(_ nodes: [ChapterNode],
                                   isExpanded: (String) -> Bool) -> [Row] {
        var rows: [Row] = []
        func walk(_ nodes: [ChapterNode], parent: String) {
            for node in nodes {
                rows.append(Row(path: node.path, isFolder: node.isFolder, parent: parent))
                if node.isFolder && isExpanded(node.path) {
                    walk(node.children, parent: node.path)
                }
            }
        }
        walk(nodes, parent: "")
        return rows
    }

    /// The step `key` takes from the row at `current` (nil or unknown
    /// starts at the first row). Up and Down stop at the ends. Right opens
    /// a closed folder, then enters an open one; Left closes an open
    /// folder, otherwise climbs to the row's parent folder.
    public static func step(_ key: Key, from current: String?, rows: [Row],
                            isExpanded: (String) -> Bool) -> Step {
        guard let first = rows.first else { return .none }
        guard let current, let index = rows.firstIndex(where: { $0.path == current }) else {
            return .focus(first.path)
        }
        let row = rows[index]
        switch key {
        case .up:
            return index > 0 ? .focus(rows[index - 1].path) : .none
        case .down:
            return index + 1 < rows.count ? .focus(rows[index + 1].path) : .none
        case .right:
            guard row.isFolder else { return .none }
            if !isExpanded(row.path) { return .expand(row.path) }
            return index + 1 < rows.count && rows[index + 1].parent == row.path
                ? .focus(rows[index + 1].path) : .none
        case .left:
            if row.isFolder && isExpanded(row.path) { return .collapse(row.path) }
            return row.parent.isEmpty ? .none : .focus(row.parent)
        }
    }
}
