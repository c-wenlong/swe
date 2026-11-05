import SWECore
import SwiftUI

/// The Compute tab's header button in the panel: the fan popover stands
/// where Usage's Refresh does, since the charts tick on their own. The
/// glyph fills while a manual speed is held, so a forgotten override
/// shows here too.
struct PanelFanButton: View {
    @Environment(AppModel.self) private var app
    @State private var showing = false
    @State private var hovering = false

    private var fans: FanControl { app.fans }

    var body: some View {
        Button(fans.isHolding ? "Fans: manual" : "Fans",
               systemImage: fans.isHolding ? "fan.fill" : "fan") { showing = true }
            .labelStyle(.iconOnly)
            .font(Theme.font(size: 14))
            .foregroundStyle(hovering ? Theme.textPrimary : Theme.textSecondary)
            .frame(width: 22, height: 22)
            .contentShape(.rect)
            .plainButton(cornerRadius: Theme.smallRadius)
            .onHover { hovering = $0 }
            // `.help`, not a tip: a tip's popover would open beside the
            // fan popover this button opens.
            .help(fans.isHolding ? "Fans: manual" : "Fans")
            .popover(isPresented: $showing, arrowEdge: .bottom) {
                FanPopover(compact: true)
                    .padding(12)
                    .frame(width: 240)
            }
            // One read when the tab's header shows, so the glyph and the
            // popover's rows aren't a stale sample.
            .task { await fans.refresh() }
    }
}
