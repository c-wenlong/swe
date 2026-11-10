import Foundation

/// The quiver state arrows, bundled as `state-<kind>.svg`. They are the
/// reusable status artwork for future client adoption:
/// red for critical failure, yellow for partial failure or attention,
/// green for fully operational, grey for interrupted or unknown. Sources
/// and the full asset set live in `assets/swe-arrows/`.
public enum StateArrows {
    public enum Kind: String, Sendable, CaseIterable {
        case red, yellow, green, grey
    }

    /// The arrow's bundled SVG, or nil when the resource is missing so a
    /// call site draws nothing rather than crash.
    public static func url(_ kind: Kind) -> URL? {
        CoreResources.url("state-\(kind.rawValue)", "svg")
    }
}
