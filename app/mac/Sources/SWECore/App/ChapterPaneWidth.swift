/// The width of the AGENTS.md Edit tab's chapter pane, which the user drags
/// wider or narrower. The minimums grow with the text zoom so filenames and
/// the KiB column stay readable, and the pane never takes the room the
/// editor needs. The view stores the value in UserDefaults.
public enum ChapterPaneWidth {
    public static let standard = 260.0
    /// The narrowest pane at 100% text zoom.
    public static let minimum = 200.0
    public static let maximum = 560.0
    /// The narrowest editor at 100% text zoom.
    public static let editorMinimum = 780.0
    /// The gap between the pane and the editor, kept out of the editor's share.
    public static let dividerWidth = 12.0
    /// One keyboard or VoiceOver step.
    public static let step = 20.0

    /// `width` held between the zoom-scaled minimum and the largest the
    /// container allows. `available` is the width of the pane, divider and
    /// editor together; nil skips the container limit. A container too
    /// narrow for both minimums keeps the pane at its minimum.
    public static func clamped(_ width: Double, zoom: Double = 1, available: Double? = nil) -> Double {
        let zoom = TextZoom.clamped(zoom)
        let low = minimum * zoom
        var high = maximum * max(zoom, 1)
        if let available, available.isFinite {
            high = min(high, available - dividerWidth - editorMinimum * zoom)
        }
        guard !width.isNaN else { return max(low, min(standard * zoom, high)) }
        return max(low, min(width, high))
    }
}

/// When an instruction editor's header drops its button words for icons.
/// At 100% zoom the full header (Raw and Rich, the Unsaved dot, Revert,
/// Save, Close) needs about 420 pt beside a readable title, so a card
/// narrower than `compactBelow` (scaled by the text zoom) goes compact.
/// A 900 pt window with the sidebar and chapter pane open leaves the
/// editor less than that.
public enum EditorHeader {
    public static let compactBelow = 640.0

    /// Whether a card `width` points wide shows icon-only buttons. An
    /// unmeasured card (0) keeps its words.
    public static func isCompact(width: Double, zoom: Double = 1) -> Bool {
        width > 0 && width < compactBelow * TextZoom.clamped(zoom)
    }
}
