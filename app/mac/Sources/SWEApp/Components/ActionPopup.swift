import AppKit
import SwiftUI

/// Actions reuse the select popover's fonts, hover rows and keyboard
/// navigation, without a selected value or an AppKit context menu.
struct PopupAction: Identifiable {
    var id: String
    var title: String
    var symbol: String
    var destructive = false
}

struct ActionOptionList: View {
    var actions: [PopupAction]
    var choose: (String) -> Void

    var body: some View {
        SelectOptionList(options: actions.map { ($0.id, $0.title) }, selection: "",
                         pick: choose,
                         symbols: Dictionary(uniqueKeysWithValues: actions.map { ($0.id, $0.symbol) }),
                         destructiveValues: Set(actions.filter(\.destructive).map(\.id)))
            .background(Theme.card)
    }
}

/// One monitor for the page's context targets. SwiftUI Buttons can
/// otherwise treat secondary clicks as their primary action before a
/// gesture recognizes them. Ordinary clicks pass through untouched.
@MainActor
final class SecondaryClickRouter {
    static let shared = SecondaryClickRouter()
    private let targets = NSHashTable<SecondaryClickView>.weakObjects()
    private var monitor: Any?
    private var consumed: (window: Int, button: Int)?

    func add(_ view: SecondaryClickView) {
        targets.add(view)
        if monitor == nil {
            monitor = NSEvent.addLocalMonitorForEvents(matching: [.rightMouseDown, .rightMouseUp, .leftMouseDown, .leftMouseUp]) { event in
                let handled = MainActor.assumeIsolated { self.route(event) == nil }
                return handled ? nil : event
            }
        }
    }

    func remove(_ view: SecondaryClickView) {
        targets.remove(view)
        if targets.allObjects.isEmpty, let monitor {
            NSEvent.removeMonitor(monitor)
            self.monitor = nil
            consumed = nil
        }
    }

    func route(_ event: NSEvent) -> NSEvent? {
        if event.type == .rightMouseUp || event.type == .leftMouseUp {
            if let consumed, consumed.window == event.windowNumber && consumed.button == event.buttonNumber {
                self.consumed = nil
                return nil
            }
            return event
        }
        guard event.type == .rightMouseDown || (event.type == .leftMouseDown && event.modifierFlags.contains(.control)) else {
            return event
        }
        let target = targets.allObjects.filter { view in
            guard view.window != nil, view.window === event.window, !view.isHiddenOrHasHiddenAncestor else { return false }
            return view.visibleRect.contains(view.convert(event.locationInWindow, from: nil))
        }.min { $0.bounds.width * $0.bounds.height < $1.bounds.width * $1.bounds.height }
        guard let target else { return event }
        consumed = (event.windowNumber, event.buttonNumber)
        target.clicked(target.convert(event.locationInWindow, from: nil))
        return nil
    }
}

final class SecondaryClickView: NSView {
    var clicked: (CGPoint) -> Void = { _ in }
    override var isFlipped: Bool { true }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}

struct SecondaryClickTarget: NSViewRepresentable {
    var clicked: (CGPoint) -> Void

    func makeNSView(context: Context) -> SecondaryClickView {
        let view = SecondaryClickView()
        view.clicked = clicked
        SecondaryClickRouter.shared.add(view)
        return view
    }

    func updateNSView(_ view: SecondaryClickView, context: Context) { view.clicked = clicked }

    static func dismantleNSView(_ view: SecondaryClickView, coordinator: ()) {
        SecondaryClickRouter.shared.remove(view)
    }
}

struct ActionContextMenu: ViewModifier {
    var actions: [PopupAction]
    var choose: (String) -> Void
    @State private var open = false
    @State private var point = CGPoint.zero

    func body(content: Content) -> some View {
        content
            .background(SecondaryClickTarget { location in
                guard !actions.isEmpty else { return }
                point = location
                open = true
            })
            .accessibilityAction(named: "Plugin actions") { open = !actions.isEmpty }
            .popover(isPresented: $open,
                     attachmentAnchor: .rect(.rect(CGRect(origin: point, size: CGSize(width: 1, height: 1))))) {
                ActionOptionList(actions: actions) { id in
                    open = false
                    DispatchQueue.main.async { choose(id) }
                }
                .onExitCommand { open = false }
            }
    }
}
