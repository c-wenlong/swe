import SwiftUI

/// The app used to show these as resting-hover pop-overs that pinned on
/// click. They are gone: the popover window covered the control it
/// explained, and the modifier's own tap ate the click meant for that
/// control — a row's checkbox spent its first press on the tip.
/// `.hoverTip` survives only as the text's VoiceOver channel, so the
/// calls keep meaning "the accessible description of this view".
private struct TipAccessibility: ViewModifier {
    var text: String
    /// Whether VoiceOver hears the text as the view's name, for a mark or
    /// icon with none of its own. Off for a view that shows text of its
    /// own: the text is then its hint.
    var replacesLabel: Bool

    func body(content: Content) -> some View {
        if replacesLabel {
            content.accessibilityLabel(text)
        } else {
            content.accessibilityHint(text)
        }
    }
}

extension View {
    /// The accessible description of the view: a VoiceOver label, or a
    /// hint after its own label when `replacesLabel` is off. Shows
    /// nothing on hover and attaches no tap, so the view keeps every
    /// click. `pinsOnClick` and `enabled` stay in the signature so the
    /// calls compile; they no longer do anything.
    func hoverTip(_ text: String, title: String? = nil, replacesLabel: Bool = true,
                  pinsOnClick: Bool = true, enabled: Bool = true) -> some View {
        modifier(TipAccessibility(text: title.map { "\($0): \(text)" } ?? text,
                                  replacesLabel: replacesLabel))
    }
}
