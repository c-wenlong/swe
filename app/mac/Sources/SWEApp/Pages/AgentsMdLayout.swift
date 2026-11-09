import SWECore
import SwiftUI

/// Layout of the AGENTS.md Edit tab: the draggable divider between the
/// chapter pane and the editor, and the editor focus mode that folds the
/// pane and the harness strip away. Both are UI layout preferences, so
/// they live in UserDefaults like the sidebar's width.
enum AgentsMdLayout {
    static let paneWidthKey = "agentsMd.chapterPaneWidth"
    static let focusKey = "agentsMd.editorFocus"
    /// Gap left of and right of the divider's hairline.
    static let dividerWidth: CGFloat = 12
    static let focusAnimation: Animation = .smooth(duration: 0.25)
}

/// The chapter pane and the editor side by side, with a divider the user
/// drags. Focus mode collapses the pane to nothing; the editor keeps the
/// whole width.
struct ChapterPaneSplit<Rail: View, Editor: View>: View {
    @AppStorage(AgentsMdLayout.paneWidthKey) private var stored = ChapterPaneWidth.standard
    @AppStorage(AgentsMdLayout.focusKey) private var focus = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The width of the whole split, to keep the editor's minimum.
    @State private var container: CGFloat = 0
    /// The width while the divider is being dragged; written to
    /// `stored` once, on drop.
    @State private var live: Double?
    @State private var dragStart: Double?
    @State private var hovering = false
    @GestureState private var dragging = false
    @ViewBuilder var rail: Rail
    @ViewBuilder var editor: Editor

    private var animation: Animation? { reduceMotion ? nil : AgentsMdLayout.focusAnimation }

    private func clamped(_ width: Double) -> Double {
        ChapterPaneWidth.clamped(width, zoom: TextZoomModel.shared.zoom,
                                 available: container > 0 ? Double(container) : nil)
    }

    var body: some View {
        let width = clamped(live ?? stored)
        HStack(alignment: .top, spacing: 0) {
            rail
                .frame(width: focus ? 0 : width)
                .frame(maxHeight: .infinity)
                .clipped()
                .opacity(focus ? 0 : 1)
                .allowsHitTesting(!focus)
                .accessibilityHidden(focus)
            divider(width: width)
                .frame(width: focus ? 0 : AgentsMdLayout.dividerWidth)
                .opacity(focus ? 0 : 1)
                .allowsHitTesting(!focus)
                .accessibilityHidden(focus)
            editor
        }
        .frame(maxHeight: .infinity)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { container = $0 }
        .animation(animation, value: focus)
    }

    /// An invisible strip between the panes: drag to resize live,
    /// double-click to reset. A hairline shows on hover and drag.
    private func divider(width: Double) -> some View {
        Color.clear
            .frame(maxHeight: .infinity)
            .contentShape(.rect)
            .overlay {
                Theme.borderHover.frame(width: 2)
                    .clipShape(.capsule)
                    .opacity(hovering || dragging ? 1 : 0)
                    .allowsHitTesting(false)
            }
            .onHover { hovering = $0 }
            .pointerStyle(.frameResize(position: .trailing))
            .animation(.easeOut(duration: Theme.fadeDuration), value: hovering || dragging)
            .highPriorityGesture(TapGesture(count: 2).onEnded { _ in
                live = nil
                withAnimation(animation) { stored = ChapterPaneWidth.standard }
            })
            .gesture(DragGesture(minimumDistance: 1, coordinateSpace: .global)
                .updating($dragging) { _, state, _ in state = true }
                .onChanged { value in
                    if dragStart == nil { dragStart = width }
                    var transaction = Transaction()
                    transaction.animation = nil
                    withTransaction(transaction) {
                        live = clamped((dragStart ?? width) + value.translation.width)
                    }
                }
                .onEnded { value in
                    stored = clamped((dragStart ?? width) + value.translation.width)
                    dragStart = nil
                    live = nil
                })
            .onDisappear { dragStart = nil; live = nil }
            .accessibilityElement()
            .accessibilityLabel("Chapter pane width")
            .accessibilityValue("\(Int(width)) points")
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment: stored = clamped(width + ChapterPaneWidth.step)
                case .decrement: stored = clamped(width - ChapterPaneWidth.step)
                default: break
                }
            }
            .accessibilityAction(named: "Reset width") { stored = ChapterPaneWidth.standard }
    }
}

/// The page-row toggle for editor focus mode (⇧⌘E). The editor's own
/// header keeps the file name, Unsaved dot and Save, and View AGENTS.md
/// stays beside this button, so nothing needed is hidden.
struct EditorFocusButton: View {
    @AppStorage(AgentsMdLayout.focusKey) private var focus = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Button {
            withAnimation(reduceMotion ? nil : AgentsMdLayout.focusAnimation) { focus.toggle() }
        } label: {
            Image(systemName: focus ? "arrow.down.right.and.arrow.up.left"
                  : "arrow.up.left.and.arrow.down.right")
        }
        .iconButton()
        .keyboardShortcut("e", modifiers: [.command, .shift])
        .help(focus ? "Show the chapters and harnesses (⇧⌘E)"
              : "Focus the editor: hide the chapters and harnesses (⇧⌘E)")
        .accessibilityLabel(focus ? "Leave editor focus" : "Focus the editor")
    }
}

/// Folds its content away in editor focus mode, shrinking the space it
/// took so the editor below moves up.
struct FocusCollapsible<Content: View>: View {
    @AppStorage(AgentsMdLayout.focusKey) private var focus = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Whether focus mode applies at all (a flat file has no focus mode).
    var enabled = true
    var spacing: CGFloat = 12
    @ViewBuilder var content: Content

    var body: some View {
        let hidden = focus && enabled
        VStack(spacing: 0) {
            if !hidden {
                content.transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .padding(.bottom, hidden ? -spacing : 0)
        .clipped()
        .animation(reduceMotion ? nil : AgentsMdLayout.focusAnimation, value: hidden)
    }
}
