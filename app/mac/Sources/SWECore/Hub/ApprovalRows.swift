import Foundation

/// The approval card's menu rows, pure so tests can feed it events. In
/// SWECore so the card and `SessionChatModel` (which row a settled card
/// lights) read one rule.
public enum ApprovalRows {
    /// The rows worth a button: labelled ones whose number the hub takes
    /// (one or two ASCII digits, as `swe.remote.protocol` checks), the same
    /// rule as the iPhone's `ApprovalMenu.rows`. A row without one would
    /// be a button whose every press the hub refuses. The row's digit is
    /// the decision its button posts.
    public static func menu(_ options: [HubApprovalOption]) -> [(n: String, label: String)] {
        options.compactMap { option in
            guard let label = option.label,
                  !label.trimmingCharacters(in: .whitespaces).isEmpty,
                  let n = option.n, (1...2).contains(n.count),
                  n.allSatisfy({ $0.isASCII && $0.isNumber })
            else { return nil }
            return (n, label)
        }
    }
}
