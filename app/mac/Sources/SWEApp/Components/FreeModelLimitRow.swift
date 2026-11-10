import SwiftUI
import SWECore

/// Free models have no percentage meter. Only a logged block is known.
struct FreeModelLimitRow: View {
    var limit: RateLimit

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            let blocked = limit.freeModelLimitReached(now: context.date)
            HStack(alignment: .firstTextBaseline) {
                Text("Free models").foregroundStyle(Theme.textSecondary)
                Spacer()
                if blocked, let reset = limit.freeModelResetAt {
                    Text("Limit reached · resets \(reset, format: .relative(presentation: .named))")
                        .foregroundStyle(Theme.dangerText)
                        .help("Observed in Devin's CLI log. Resets at \(reset.formatted(date: .abbreviated, time: .shortened)).")
                } else {
                    Text("Unknown").foregroundStyle(Theme.textSecondary)
                        .help("Devin does not report the remaining free-model allowance. A reached limit appears after the CLI logs it.")
                }
            }
            .font(Theme.caption.monospacedDigit())
            .accessibilityElement(children: .combine)
        }
    }
}
