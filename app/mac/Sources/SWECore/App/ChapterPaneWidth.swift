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
            high = min(high, available - editorMinimum * zoom)
        }
        guard !width.isNaN else { return max(low, min(standard * zoom, high)) }
        return max(low, min(width, high))
    }
}
