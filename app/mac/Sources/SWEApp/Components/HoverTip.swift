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
///
/// The pin tap is a local event monitor on the anchor's window, not a
/// gesture: it watches the click go by and pins, so it can never take
/// one either. A TapGesture — even a simultaneous one — raced the
/// control underneath and won, which is what spent a Button's first
/// click on the tip.
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
    /// resting hover but never pins. Clicks always reach the view now;
    /// the flag only says whether a click also pins the tip.
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
            .background(AnchorWindow(window: $anchorWindow,
                                     watchesPresses: pinsOnClick, pinTap: pinTap))
            .contentShape(.rect)
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
            // The press monitor hears real mice only: VoiceOver's press
            // goes through this named action instead.
            .accessibilityAction(named: Text("Pin the tip")) { pinTap() }
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
        // A popover can't present in an offscreen test window, so tests
        // watch this post to know a click really pinned the tip.
        if pinned {
            NotificationCenter.default.post(name: .hoverTipPinned, object: nil)
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

extension Notification.Name {
    /// Posted when a click pins a tip open. Nothing in the app listens;
    /// tests observe it because a popover cannot present offscreen.
    static let hoverTipPinned = Notification.Name("SWEHoverTipPinned")
}

/// An invisible NSView behind the tipped content. It reports which
/// window it sits in — how the tip finds its popover window to flag —
/// and, for a tip that pins on click, watches the window's left
/// mouse-downs for one inside the anchor's cell. A monitor only ever
/// observes the event, so the press always goes on to the view.
private struct AnchorWindow: NSViewRepresentable {
    @Binding var window: NSWindow?
    /// Off for a tip that never pins: no monitor is installed at all.
    var watchesPresses: Bool
    var pinTap: () -> Void

    func makeNSView(context: Context) -> NSView {
        let view = ReporterView()
        view.report = { window = $0 }
        view.watchesPresses = watchesPresses
        view.pinTap = pinTap
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        let view = nsView as? ReporterView
        view?.report = { window = $0 }
        view?.watchesPresses = watchesPresses
        view?.pinTap = pinTap
    }

    private final class ReporterView: NSView {
        var report: ((NSWindow?) -> Void)?
        var watchesPresses = false
        var pinTap: (() -> Void)?
        private var monitor: Any?

        override func viewDidMoveToWindow() {
            report?(window)
            if window != nil, monitor == nil, watchesPresses {
                monitor = NSEvent.addLocalMonitorForEvents(
                    matching: .leftMouseDown
                ) { [weak self] event in
                    if self?.claims(event) == true { self?.pinTap?() }
                    return event
                }
            }
            if window == nil, let monitor {
                NSEvent.removeMonitor(monitor)
                self.monitor = nil
            }
        }

        override func layout() {
            super.layout()
            report?(window)
        }

        /// Whether a press belongs to this tip: in this window and inside
        /// the anchor's own bounds — the same answer a tap gesture's hit
        /// test gave, minus the claim. The press goes on to the view
        /// either way.
        private func claims(_ event: NSEvent) -> Bool {
            guard event.window === window else { return false }
            return bounds.contains(convert(event.locationInWindow, from: nil))
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
