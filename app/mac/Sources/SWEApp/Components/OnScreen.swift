import AppKit
import SwiftUI

extension View {
    /// Calls `action` with whether this view can be seen: it is in the
    /// hierarchy and its window is visible (not closed, minimised, hidden
    /// with the app, ordered out like the menu bar panel, or fully covered).
    /// `onAppear` alone says yes for a page in a minimised window or a
    /// hidden panel, so work that only matters on screen (a poll, a fast
    /// sampler) follows this instead.
    func onScreenChange(_ action: @escaping @MainActor (Bool) -> Void) -> some View {
        modifier(OnScreenModifier(action: action))
    }
}

private struct OnScreenModifier: ViewModifier {
    let action: @MainActor (Bool) -> Void
    @State private var appeared = false
    @State private var windowVisible = false

    func body(content: Content) -> some View {
        content
            .background(OnScreenProbe { windowVisible = $0 })
            .onAppear { appeared = true }
            .onDisappear {
                appeared = false
                // The view is leaving: say so now, since an onChange
                // may never run for a view on its way out.
                action(false)
            }
            .onChange(of: appeared && windowVisible) { _, visible in action(visible) }
    }
}

/// A zero-size AppKit view that reports its window's visibility: once
/// when it joins (or leaves) a window and on every occlusion change.
private struct OnScreenProbe: NSViewRepresentable {
    let report: @MainActor (Bool) -> Void

    func makeNSView(context: Context) -> ProbeView {
        let view = ProbeView()
        view.report = report
        return view
    }

    func updateNSView(_ view: ProbeView, context: Context) {
        view.report = report
    }

    static func dismantleNSView(_ view: ProbeView, coordinator: ()) {
        view.stop()
    }

    final class ProbeView: NSView {
        var report: (@MainActor (Bool) -> Void)?
        private var observer: NSObjectProtocol?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            stop()
            if let window {
                observer = NotificationCenter.default.addObserver(
                    forName: NSWindow.didChangeOcclusionStateNotification, object: window,
                    queue: .main
                ) { [weak self] _ in
                    MainActor.assumeIsolated { self?.send() }
                }
            }
            send()
        }

        func stop() {
            if let observer { NotificationCenter.default.removeObserver(observer) }
            observer = nil
        }

        /// Deferred a turn: this can run inside a SwiftUI layout pass,
        /// where writing view state is not allowed.
        private func send() {
            let visible = window?.occlusionState.contains(.visible) ?? false
            DispatchQueue.main.async { [weak self] in self?.report?(visible) }
        }
    }
}
