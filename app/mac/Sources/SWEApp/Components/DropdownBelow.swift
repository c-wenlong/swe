import SwiftUI

extension View {
    /// Floats `content` below this view: its top sits `gap` points under
    /// this view's bottom edge and its width matches this view's. The
    /// overlay takes no layout space, so whatever hosts it never resizes
    /// for it. Use it for suggestion lists and small dropdowns inside a
    /// fixed-size card.
    func dropdownBelow<Content: View>(
        isPresented: Bool, gap: CGFloat = 4,
        @ViewBuilder content: @escaping () -> Content
    ) -> some View {
        modifier(DropdownBelow(
            isPresented: isPresented, gap: gap, overlay: content))
    }
}

private struct DropdownBelow<Overlay: View>: ViewModifier {
    var isPresented: Bool
    var gap: CGFloat
    @ViewBuilder var overlay: () -> Overlay
    @State private var height: CGFloat = 0

    func body(content: Content) -> some View {
        content
            .onGeometryChange(for: CGFloat.self) { $0.size.height }
                action: { height = $0 }
            // The offset pushes the content's top edge down past the
            // view's bottom; nothing here can land above the view.
            .overlay(alignment: .topLeading) {
                if isPresented {
                    overlay()
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .offset(y: height + gap)
                }
            }
    }
}
