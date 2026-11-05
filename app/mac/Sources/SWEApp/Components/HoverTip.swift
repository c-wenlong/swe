import AppKit
import SwiftUI

/// The app's small "what is this" affordance: a tip that opens after the
/// pointer rests a beat, hides when the pointer leaves, and pins open on
/// click (a second click, or one outside, dismisses it). Used where
/// `.help` would hide the answer behind a delay nobody waits out.
///
/// A `.popover` is its own window on top of the app's: a tip overlapping
/// a control (it flips below its anchor when the room above runs out)
/// owns every click inside its frame, so the first press used to be
/// spent on the tip instead of the control underneath. Every tip window
/// is flagged `ignoresMouseEvents` once it attaches, so clicks pass
/// through to whatever the bubble happens to cover.
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
    /// row, a mark inside a Button's label): the tip still opens on a
    /// resting hover but never pins, and attaches no live tap, so the
    /// click reaches the row or Button underneath.
    var pinsOnClick = true
    /// Off while the row is mid-drag: an open tip would cover the rows
    /// the pointer is choosing between. Gates behaviour only — the view
    /// tree never changes shape, or an in-flight gesture on the row
    /// would be torn down with the branch.
    var enabled = true

    @State private var open = false
    /// Opened by a click, so leaving the icon doesn't close it.
    @State private var pinned = false
    @State private var rest: Task<Void, Never>?
    @State private var hovering = false
    /// The anchor's window, found by a hidden NSView inside the content:
    /// how the popover's window gets flagged once it attaches.
    @State private var anchorWindow: NSWindow?
    /// The window's children seen before the tip opened — the tip's own
    /// popover is the new one after `open` flips.
    @State private var childrenBeforeOpen: Set<ObjectIdentifier> = []

    func body(content: Content) -> some View {
        content
            .background(AnchorWindow(window: $anchorWindow))
            .contentShape(.rect)
            // The tap pins the tip; a text selection would take it first.
            .textSelection(.disabled)
            // A tip that never pins attaches no live tap at all: even a
            // tap that ignores the click claims it, so a tip inside a
            // Button's label would leave the Button dead. Masking keeps
            // the view tree one shape either way.
            .gesture(TapGesture().onEnded { pinTap() },
                     including: pinsOnClick ? .all : .subviews)
            .onHover { inside in
                guard enabled, inside != hovering else { return }
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
            .onChange(of: open) { _, open in
                if !open { pinned = false; return }
                flagTipWindow()
            }
            .modifier(TipAccessibility(text: title.map { "\($0): \(text)" } ?? text,
                                       replacesLabel: replacesLabel))
            .accessibilityAddTraits(pinsOnClick ? .isButton : [])
            .popover(isPresented: $open, arrowEdge: .bottom) {
                tipContent
            }
            .onChange(of: enabled) { _, on in
                // Disabled mid-hover or mid-drag: drop everything so a
                // stale popover or cursor doesn't outlive its reason.
                guard !on else { return }
                rest?.cancel()
                if hovering, pinsOnClick { NSCursor.pop() }
                hovering = false
                open = false
                pinned = false
            }
            .onDisappear {
                rest?.cancel()
                if hovering, pinsOnClick { NSCursor.pop() }
            }
    }

    private func pinTap() {
        guard enabled, pinsOnClick else { return }
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

    /// Flags the tip's own popover window `ignoresMouseEvents` so it can
    /// never eat a click — the whole point of showing a tip is to leave
    /// the control it explains usable. The window attaches a run-loop
    /// turn after `open` flips, so this keeps checking briefly; a child
    /// must be new since the tip opened AND host a `TipBubble`, so an
    /// unrelated popover can't be flagged by mistake.
    private func flagTipWindow() {
        guard let window = anchorWindow else { return }
        childrenBeforeOpen = Set((window.childWindows ?? []).map { ObjectIdentifier($0) })
        Task { @MainActor in
            for _ in 0..<20 where open {
                for child in anchorWindow?.childWindows ?? [] {
                    let id = ObjectIdentifier(child)
                    guard !childrenBeforeOpen.contains(id),
                          Self.hostsTipBubble(child)
                    else { continue }
                    child.ignoresMouseEvents = true
                    childrenBeforeOpen.insert(id)
                }
                try? await Task.sleep(for: .milliseconds(25))
            }
        }
    }

    /// Whether the window's content is a `TipBubble`, checked on the
    /// content view controller's type and on the hosted view tree —
    /// whichever SwiftUI's popover presentation ends up using.
    private static func hostsTipBubble(_ window: NSWindow) -> Bool {
        if let vc = window.contentViewController,
           String(describing: type(of: vc)).contains("TipBubble") { return true }
        var views = [window.contentView].compactMap { $0 }
        while let view = views.popLast() {
            if String(describing: type(of: view)).contains("TipBubble") { return true }
            views.append(contentsOf: view.subviews)
        }
        return false
    }

    private var tipContent: some View {
        TipBubble(title: title, text: text)
    }
}

/// The bubble shown in a tip's popover. Its name doubles as the marker
/// the flagging pass looks for in the popover window's content view
/// controller, so other popovers keep their clicks.
private struct TipBubble: View {
    var title: String?
    var text: String

    var body: some View {
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

/// An invisible NSView behind the tipped content that reports which
/// window it sits in — how the tip finds its popover window to flag.
private struct AnchorWindow: NSViewRepresentable {
    @Binding var window: NSWindow?

    func makeNSView(context: Context) -> NSView {
        let view = ReporterView()
        view.report = { window = $0 }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        (nsView as? ReporterView)?.report = { window = $0 }
    }

    private final class ReporterView: NSView {
        var report: ((NSWindow?) -> Void)?
        override func viewDidMoveToWindow() { report?(window) }
        override func layout() {
            super.layout()
            report?(window)
        }
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
