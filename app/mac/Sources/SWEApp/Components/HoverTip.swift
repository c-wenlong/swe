import AppKit
import SwiftUI

/// The app's small "what is this" affordance: a tip that opens after the
/// pointer rests a beat, hides when the pointer leaves, and pins open on
/// click (a second click, or one outside, dismisses it). Used where
/// `.help` would hide the answer behind a delay nobody waits out.
struct HoverTip: ViewModifier {
    var text: String
    /// A bold first line above the text, such as a skill's name.
    var title: String? = nil
    /// Whether VoiceOver hears the tip as the view's name, for a mark or
    /// icon with none of its own. Off for a view that shows text of its
    /// own (a figure, a name, a button title): the tip is then its hint,
    /// so the text it shows is still read.
    var replacesLabel = true
    /// Off for views where a click means something else (a draggable
    /// row): the tip still opens on a resting hover but never pins, so
    /// the first press is never spent dismissing it.
    var pinsOnClick = true
    /// Off while the row is mid-drag: an open tip would cover the rows
    /// the pointer is choosing between.
    var enabled = true

    @State private var open = false
    /// Opened by a click, so leaving the icon doesn't close it.
    @State private var pinned = false
    @State private var rest: Task<Void, Never>?
    @State private var hovering = false

    func body(content: Content) -> some View {
        Group {
            if enabled {
                content
                    .contentShape(.rect)
                    // The tap pins the tip; a text selection would take it first.
                    .textSelection(.disabled)
                    .onTapGesture {
                        guard pinsOnClick else { return }
                        rest?.cancel()
                        if open, !pinned {
                            pinned = true
                        } else if pinned {
                            open = false
                        } else {
                            open = true
                            pinned = true
                        }
                    }
                    .onHover { inside in
                        if inside == hovering { return }
                        hovering = inside
                        if inside {
                            if pinsOnClick { NSCursor.pointingHand.push() }
                            rest = Task {
                                try? await Task.sleep(for: .milliseconds(150))
                                guard !Task.isCancelled else { return }
                                open = true
                            }
                        } else {
                            if pinsOnClick { NSCursor.pop() }
                            rest?.cancel()
                            if !pinned { open = false }
                        }
                    }
                    // A click outside the popover also dismisses it; the pin goes
                    // with it so the next hover-only open closes on leaving.
                    .onChange(of: open) { _, open in if !open { pinned = false } }
                    .modifier(TipAccessibility(text: title.map { "\($0): \(text)" } ?? text,
                                               replacesLabel: replacesLabel))
                    .accessibilityAddTraits(.isButton)
                    .popover(isPresented: $open, arrowEdge: .bottom) {
                        tipContent
                    }
                    .onDisappear {
                        rest?.cancel()
                        if hovering, pinsOnClick { NSCursor.pop() }
                    }
            } else {
                content
            }
        }
        .onChange(of: enabled) { _, on in
            // Disabled mid-hover or mid-drag: drop everything so a stale
            // popover or cursor doesn't outlive the reason it showed.
            guard !on else { return }
            rest?.cancel()
            if hovering, pinsOnClick { NSCursor.pop() }
            hovering = false
            open = false
            pinned = false
        }
    }

    private var tipContent: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let title {
                Text(title).font(Theme.caption.weight(.semibold)).foregroundStyle(Theme.textPrimary)
            }
            Text(text)
                .font(Theme.caption)
                .foregroundStyle(Theme.textSecondary)
                .lineLimit(nil)
        }
        .frame(maxWidth: title == nil ? 260 : 320, alignment: .leading)
        .fixedSize(horizontal: false, vertical: true)
        .padding(10)
    }
}

/// The tip as the view's VoiceOver label, or as a hint after its own.
private struct TipAccessibility: ViewModifier {
    var text: String
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
    /// A resting-hover and click tip, for small info marks and controls
    /// that need an explanation a `.help` tooltip would hide.
    func hoverTip(_ text: String, title: String? = nil, replacesLabel: Bool = true,
                  pinsOnClick: Bool = true, enabled: Bool = true) -> some View {
        modifier(HoverTip(text: text, title: title, replacesLabel: replacesLabel,
                          pinsOnClick: pinsOnClick, enabled: enabled))
    }
}
