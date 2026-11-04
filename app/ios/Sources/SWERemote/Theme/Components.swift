import SwiftUI

// MARK: - Surfaces

/// A hairline card on `Theme.card`, 16pt radius: the Mac app's `Card`.
struct Card<Content: View>: View {
    var padding: CGFloat = 16
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }
}

/// Rows stacked in one card with hairlines between them, so a list
/// reads as one surface (the Mac Sessions list on a phone).
struct CardList<Data: RandomAccessCollection, Row: View>: View where Data.Element: Identifiable {
    let data: Data
    @ViewBuilder var row: (Data.Element) -> Row

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(data.enumerated()), id: \.element.id) { index, element in
                if index > 0 { Rectangle().fill(Theme.border).frame(height: 1).padding(.leading, Theme.rowHorizontal) }
                row(element)
            }
        }
        .frame(maxWidth: .infinity)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
        .clipShape(.rect(cornerRadius: Theme.cardRadius))
    }
}

/// Sentence-case group heading above a card list.
struct GroupLabel: View {
    var text: String
    var color: Color = Theme.textSecondary
    var trailing: String?

    var body: some View {
        HStack {
            Text(text).font(Theme.groupLabel).foregroundStyle(color).lineLimit(1, larger: 2)
            Spacer(minLength: 8)
            if let trailing { Text(trailing).font(Theme.caption).foregroundStyle(Theme.textMuted) }
        }
        .padding(.horizontal, 4)
        .padding(.top, 10)
    }
}

/// The scroll container every list page uses: vertical only, content
/// pinned to the screen width so nothing can push it sideways.
struct PageScroll<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView(.vertical) {
            LazyVStack(alignment: .leading, spacing: 8) { content }
                .padding(.horizontal, Theme.gutter)
                .padding(.bottom, 24)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollBounceBehavior(.basedOnSize, axes: .horizontal)
        .background(Theme.wash)
    }
}

// MARK: - Status and tags

struct StatusDot: View {
    var status: SessionStatus
    /// A follow-up nobody answered for days: grey, not amber.
    var stale = false
    var body: some View {
        Circle().fill(stale ? Theme.borderStrong : Theme.status(status)).frame(width: Theme.statusDot, height: Theme.statusDot)
    }
}

/// After a session row's title: activity this phone hasn't read. Set
/// on the title's baseline and raised to the middle of its lowercase
/// letters, so it follows the title at any text size.
struct UnreadDot: View {
    @ScaledMetric(relativeTo: .subheadline) private var side: CGFloat = 7

    var body: some View {
        Circle().fill(Theme.accent)
            .frame(width: side, height: side)
            .alignmentGuide(.firstTextBaseline) { $0[.bottom] + side * 0.2 }
            // A bare shape is no accessibility element; this one reads.
            .accessibilityElement()
            .accessibilityLabel("Unread")
    }
}

/// The Mac app's `Badge`: a small pill, hairline on `subtle`.
struct Badge: View {
    var text: String
    var tint: Color = Theme.textLabel
    init(_ text: String, tint: Color = Theme.textLabel) {
        self.text = text
        self.tint = tint
    }

    var body: some View {
        Text(text)
            .font(Theme.badge)
            .foregroundStyle(tint)
            .lineLimit(1)
            .padding(.horizontal, 8)
            .padding(.vertical, 2)
            .background(Theme.subtle, in: .capsule)
            .overlay(Capsule().strokeBorder(Theme.borderStrong, lineWidth: 1))
    }
}

/// A filter chip with a count; selected chips take the raised fill.
/// A nil count draws the title alone — a chip whose list arrives on
/// demand (Closed) shows no number until it has one.
struct FilterChip: View {
    var title: String
    var count: Int?
    var selected: Bool
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Text(title)
                if let count {
                    Text("\(count)").foregroundStyle(selected ? Theme.textSecondary : Theme.textMuted).monospacedDigit()
                }
            }
            .font(Theme.control)
            .foregroundStyle(selected ? Theme.textPrimary : Theme.textSecondary)
            .padding(.horizontal, 12)
            .scaledMinHeight(Theme.controlHeight)
            .background(selected ? Theme.selected : Theme.card, in: .rect(cornerRadius: Theme.controlRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(selected ? Theme.borderStrong : Theme.border, lineWidth: 1))
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Buttons

/// Ink on light, white on dark; never green. Full width at 44pt.
struct PrimaryButtonStyle: ButtonStyle {
    var compact = false
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(compact ? Theme.control : Theme.button)
            .foregroundStyle(Theme.primaryForeground)
            .centredWhenWrapped()
            .padding(.horizontal, compact ? 12 : 16)
            .frame(maxWidth: compact ? nil : .infinity)
            .scaledMinHeight(compact ? Theme.controlHeight : Theme.buttonHeight)
            .background(Theme.primaryBackground, in: .rect(cornerRadius: compact ? Theme.controlRadius : Theme.blockRadius))
            .opacity(configuration.isPressed ? 0.85 : enabled ? 1 : 0.45)
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    var compact = false
    var destructive = false
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(compact ? Theme.control : Theme.button)
            .foregroundStyle(destructive ? Theme.dangerText : Theme.textPrimary)
            .centredWhenWrapped()
            .padding(.horizontal, compact ? 12 : 16)
            .frame(maxWidth: compact ? nil : .infinity)
            .scaledMinHeight(compact ? Theme.controlHeight : Theme.buttonHeight)
            .background(configuration.isPressed ? Theme.selected : Theme.card,
                        in: .rect(cornerRadius: compact ? Theme.controlRadius : Theme.blockRadius))
            .overlay(RoundedRectangle(cornerRadius: compact ? Theme.controlRadius : Theme.blockRadius)
                .strokeBorder(Theme.borderStrong, lineWidth: 1))
            .opacity(enabled ? 1 : 0.45)
    }
}

extension View {
    func primaryButton(compact: Bool = false) -> some View { buttonStyle(PrimaryButtonStyle(compact: compact)) }
    func secondaryButton(compact: Bool = false, destructive: Bool = false) -> some View {
        buttonStyle(SecondaryButtonStyle(compact: compact, destructive: destructive))
    }

    /// The Mac app's `sweField`: 44pt, hairline, field fill.
    func sweField(monospaced: Bool = false) -> some View {
        self
            .font(monospaced ? Theme.monoBody : Theme.body)
            .foregroundStyle(Theme.textPrimary)
            .padding(.horizontal, 12)
            .scaledMinHeight(Theme.fieldHeight)
            .background(Theme.field, in: .rect(cornerRadius: Theme.blockRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
    }
}

// MARK: - States

/// Empty, error and loading states share one quiet card.
struct StateCard: View {
    enum Tone { case plain, error }
    var icon: String
    var title: String
    var message: String?
    var tone: Tone = .plain
    var actionTitle: String?
    var action: (() -> Void)?

    var body: some View {
        Card(padding: 20) {
            VStack(alignment: .leading, spacing: 8) {
                Label(title, systemImage: icon)
                    .font(Theme.rowTitle)
                    .foregroundStyle(tone == .error ? Theme.dangerText : Theme.textPrimary)
                if let message {
                    Text(inlineCode: message).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let actionTitle, let action {
                    Button(actionTitle, action: action).secondaryButton(compact: true).padding(.top, 4)
                }
            }
        }
    }
}

/// Over what a failed refresh left on screen: the rows stay, and this
/// says why they may be out of date, in the hub's words. Two lines at
/// the default text size; more as the text grows, so the advice at the
/// end ("Check that Tailscale is on") isn't the part cut off.
struct StaleNotice: View {
    let error: String

    var body: some View {
        Label { Text(inlineCode: "Couldn't refresh: \(error)") } icon: { Image(systemName: "exclamationmark.triangle") }
            .font(Theme.caption).foregroundStyle(Theme.warningText)
            .lineLimit(2, larger: 6)
            .fixedSize(horizontal: false, vertical: true)
    }
}

extension Text {
    /// A hub or app message, its backtick spans drawn as inline code.
    init(inlineCode message: String) { self.init(Markdown.inlineCode(message)) }
}

struct LoadingRow: View {
    var text: String
    var body: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text(text).font(Theme.caption).foregroundStyle(Theme.textSecondary)
        }
        .padding(.vertical, 24)
        .frame(maxWidth: .infinity)
    }
}

/// A placeholder bar with a light band sweeping across it, for skeleton
/// loading states laid out like what they stand in for. The band is an
/// offset, so the sweep never lays the page out again.
struct SkeletonBlock: View {
    var width: CGFloat?
    var height: CGFloat
    var radius: CGFloat = 6

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var swept = false

    var body: some View {
        RoundedRectangle(cornerRadius: radius)
            .fill(Theme.border)
            .overlay {
                if !reduceMotion {
                    GeometryReader { geometry in
                        LinearGradient(colors: [.clear, Theme.borderStrong.opacity(0.7), .clear],
                                       startPoint: .leading, endPoint: .trailing)
                            .frame(width: 160)
                            .offset(x: swept ? geometry.size.width : -160)
                    }
                }
            }
            .clipShape(.rect(cornerRadius: radius))
            .frame(width: width, height: height)
            .frame(maxWidth: width == nil ? .infinity : nil, alignment: .leading)
            .onAppear {
                withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) { swept = true }
            }
            .accessibilityHidden(true)
    }
}

/// The verbs the working indicator cycles through — the same list the
/// desktop client uses, so every surface says the agent is working the
/// same way.
enum WorkingVerbs {
    static let all = [
        "Investigating", "Tinkering", "Building",
        "Thinking", "Working", "Analyzing",
    ]
}

/// A cycling verb with a light band sweeping diagonally across it — the
/// shared "the agent is working" readout. The widest verb is laid out
/// invisibly beside it so a word swap never resizes the pill it sits in.
///
/// It stays on screen for a whole agent turn, so it costs almost nothing
/// per frame: the word swaps on a periodic timeline (one body evaluation
/// per verb, not per display frame), and each verb's band is one linear
/// offset animation that SwiftUI interpolates without evaluating a body.
/// Nothing animates while the word rests unlit.
struct WorkingVerb: View {
    /// Seconds a verb stays up.
    static let verbSeconds: Double = 2.4
    /// Share of a verb's stay the band takes to cross it; the word then
    /// rests unlit until the next verb slides in.
    static let sweepShare: Double = 0.7
    /// Half the band's width as a fraction of the word's width.
    static let bandHalf: Double = 0.26

    var body: some View {
        TimelineView(Self.schedule(now: .now)) { context in
            let index = Self.index(at: context.date)
            ZStack(alignment: .leading) {
                Text("\(Self.widest)…").opacity(0)
                SweepingVerb(word: WorkingVerbs.all[index], since: Self.slotStart(at: context.date))
                    .id(index)
                    .transition(.opacity)
            }
            .animation(.easeInOut(duration: 0.3), value: index)
        }
    }

    /// One tick per verb, on the same wall-clock boundaries `index(at:)`
    /// counts, so every tick lands on a new word.
    static func schedule(now: Date) -> PeriodicTimelineSchedule {
        let t = now.timeIntervalSinceReferenceDate
        let start = (t / verbSeconds).rounded(.down) * verbSeconds
        return .periodic(from: Date(timeIntervalSinceReferenceDate: start), by: verbSeconds)
    }

    /// The verb on screen at `date`. The small nudge keeps a tick that
    /// lands on a boundary from rounding down to the previous verb.
    static func index(at date: Date) -> Int {
        let slot = slot(at: date)
        let n = WorkingVerbs.all.count
        return ((slot % n) + n) % n
    }

    /// When the verb on screen at `date` came up.
    static func slotStart(at date: Date) -> Date {
        Date(timeIntervalSinceReferenceDate: Double(slot(at: date)) * verbSeconds)
    }

    private static func slot(at date: Date) -> Int {
        Int((date.timeIntervalSinceReferenceDate / verbSeconds + 1e-6).rounded(.down))
    }

    /// How far the band is across a verb that came up at `since`, at
    /// `now`: 0 as it comes up, 1 once it has crossed and the word rests.
    /// The pill can appear partway through a verb; its band then starts
    /// where it would be, not back at the left.
    static func sweep(since: Date, now: Date) -> Double {
        let elapsed = max(0, now.timeIntervalSince(since))
        return min(1, elapsed / (verbSeconds * sweepShare))
    }

    /// The verb that pins the pill's width.
    private static var widest: String {
        WorkingVerbs.all.max { $0.count < $1.count } ?? "Working"
    }
}

/// One verb's turn: the band crosses it once, linearly, and stops. It
/// starts from where the band is when it appears, so a pill that shows
/// up late in a verb's turn still finishes the sweep on time.
private struct SweepingVerb: View {
    let word: String
    @State private var sweep: Double

    init(word: String, since: Date) {
        self.word = word
        _sweep = State(initialValue: WorkingVerb.sweep(since: since, now: .now))
    }

    var body: some View {
        VerbShimmer(word: word, sweep: sweep)
            .onAppear {
                guard sweep < 1 else { return }
                let left = (1 - sweep) * WorkingVerb.verbSeconds * WorkingVerb.sweepShare
                withAnimation(.linear(duration: left)) { sweep = 1 }
            }
    }
}

/// `word` in the quiet colour, its brighter copy revealed only inside a
/// diagonal band, with a faint bloom so the sheen reads as light rather
/// than a recolour. `sweep` puts the band between off-left (0) and
/// off-right (1).
///
/// The band is a fixed gradient on a strip three words wide, slid by an
/// offset that is linear in `sweep`. Animating `sweep` therefore animates
/// only the offset, which SwiftUI interpolates frame by frame on its own;
/// the band's stops are never rebuilt mid-sweep.
struct VerbShimmer: View {
    let word: String
    let sweep: Double

    var body: some View {
        Text("\(word)…")
            .foregroundStyle(Theme.textSecondary)
            .overlay {
                Text("\(word)…")
                    .foregroundStyle(Theme.textPrimary)
                    .shadow(color: Theme.textPrimary.opacity(0.5), radius: 3)
                    .mask {
                        GeometryReader { geometry in
                            band(size: geometry.size)
                        }
                    }
            }
            .accessibilityHidden(true)
    }

    /// How many word widths the band's strip spans. It has to reach the
    /// word's right edge at the leftmost offset.
    static let stripWidths: Double = 3
    /// Where the band's centre sits on the strip's own gradient.
    static let stripCentre: Double = 0.5

    /// The band's centre on the word's diagonal (0 at the top-left corner,
    /// 1 at the bottom-right), from just off-left to just off-right.
    static func centre(sweep: Double) -> Double {
        let half = WorkingVerb.bandHalf
        return -half + (1 + half * 2) * sweep
    }

    /// The strip's offset that puts the band's centre at `centre(sweep:)`
    /// on a word of `size`. A gradient aimed along the word's diagonal
    /// (w, h) moves by w / (w² + h²) of its length per point it slides
    /// right, so the offset is the centre's distance from the strip's own
    /// centre over that.
    static func offset(sweep: Double, size: CGSize) -> CGFloat {
        let w = Double(size.width), h = Double(size.height)
        guard w > 0 else { return 0 }
        return CGFloat((centre(sweep: sweep) - stripCentre) * (w * w + h * h) / w)
    }

    /// A clear→white→clear gradient along the word's diagonal, on a strip
    /// `stripWidths` words wide: the end point sits one word width and one
    /// word height from the start, so the angle matches the word's own
    /// diagonal and every stop stays inside 0...1.
    private func band(size: CGSize) -> some View {
        let half = WorkingVerb.bandHalf
        let c = Self.stripCentre
        return LinearGradient(
            stops: [
                .init(color: .clear, location: c - half),
                .init(color: .white, location: c),
                .init(color: .clear, location: c + half),
            ],
            startPoint: .topLeading,
            endPoint: UnitPoint(x: 1 / Self.stripWidths, y: 1)
        )
        .frame(width: size.width * Self.stripWidths, height: size.height)
        .offset(x: Self.offset(sweep: sweep, size: size))
    }
}

/// The elapsed count under the working pill — "0:42" ticking once a
/// second while the agent runs.
struct WorkingClock: View {
    /// When the turn started.
    let since: Date

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            Text(Self.format(context.date.timeIntervalSince(since)))
        }
        .font(Theme.small)
        .monospacedDigit()
        .foregroundStyle(Theme.textSecondary)
    }

    /// `s` seconds as "m:ss".
    static func format(_ s: TimeInterval) -> String {
        let t = max(0, Int(s))
        return String(format: "%d:%02d", t / 60, t % 60)
    }
}

// MARK: - Harness logos

/// A harness shown as its logo, the way the Mac app's `HarnessIcon` does:
/// colour logos as they are, single-colour marks in the text colour. A
/// harness with no bundled logo keeps its name. Logos come from
/// script/import-harness-icons.py.
struct HarnessIcon: View {
    var harness: String?
    var name: String
    var size: CGFloat = 14
    /// `size` is the logo at the default text size; it grows with the text beside it.
    @ScaledMetric(relativeTo: .footnote) private var scale: CGFloat = 1

    init(harness: String?, name: String, size: CGFloat = 14) {
        self.harness = harness
        self.name = name
        self.size = size
    }

    /// Other names the same harness goes by (as in SWECore's HarnessIcons).
    static let aliases = ["claude-code": "claude", "claude-desktop": "claude", "github-copilot": "copilot",
                          "factory": "droid", "qwen": "qwen-code", "mistral": "mistral-vibe",
                          "codebuff": "freebuff", "command-code": "commandcode", "grok-bot": "grok",
                          "muse": "meta"]

    static func assetName(_ harness: String?) -> String? {
        guard let h = harness?.lowercased(), !h.isEmpty else { return nil }
        let id = aliases[h] ?? h
        let name = "harness-\(id)"
        return UIImage(named: name) == nil ? nil : name
    }

    var body: some View {
        if let asset = Self.assetName(harness) {
            Image(asset)
                .resizable()
                .scaledToFit()
                .frame(width: size * scale, height: size * scale)
                .foregroundStyle(Theme.textPrimary)
                .accessibilityLabel(name)
        } else {
            Text(name).foregroundStyle(Theme.harness(harness))
        }
    }
}

extension StateCard {
    /// A page whose first load failed, with a way to try again.
    static func unreachable(_ error: String, retry: @escaping () -> Void) -> StateCard {
        StateCard(icon: "wifi.exclamationmark", title: "Can't reach the hub",
                  message: HubError.detail(underUnreachableTitle: error), tone: .error,
                  actionTitle: "Try again", action: retry)
    }
}

// MARK: - Larger text sizes

/// Views in a row that wraps onto more lines when it runs out of width,
/// like words in a paragraph; each line is centred vertically, as an
/// HStack's is. A view wider than a whole line gets the line's width.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6
    var lineSpacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let lines = arrange(proposal.width ?? .infinity, subviews)
        let height = lines.reduce(0) { $0 + $1.height } + lineSpacing * CGFloat(max(0, lines.count - 1))
        return CGSize(width: lines.map(\.width).max() ?? 0, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for line in arrange(bounds.width, subviews) {
            var x = bounds.minX
            for item in line.items {
                subviews[item.index].place(at: CGPoint(x: x, y: y + (line.height - item.size.height) / 2),
                                           proposal: ProposedViewSize(item.size))
                x += item.size.width + spacing
            }
            y += line.height + lineSpacing
        }
    }

    private struct Line {
        var items: [(index: Int, size: CGSize)] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(_ maxWidth: CGFloat, _ subviews: Subviews) -> [Line] {
        var lines: [Line] = []
        var line = Line()
        for (index, subview) in subviews.enumerated() {
            var size = subview.sizeThatFits(.unspecified)
            if size.width > maxWidth { size = subview.sizeThatFits(ProposedViewSize(width: maxWidth, height: nil)) }
            if !line.items.isEmpty && line.width + spacing + size.width > maxWidth {
                lines.append(line)
                line = Line()
            }
            line.width += (line.items.isEmpty ? 0 : spacing) + size.width
            line.height = max(line.height, size.height)
            line.items.append((index, size))
        }
        if !line.items.isEmpty { lines.append(line) }
        return lines
    }
}

/// An HStack at the default text size; above it, a `FlowLayout`, so
/// filter chips and a row's "·" details wrap onto another line instead
/// of squeezing their words letter by letter or cutting off.
struct WrappingHStack<Content: View>: View {
    var spacing: CGFloat = 6
    var lineSpacing: CGFloat = 4
    @ViewBuilder var content: Content
    @Environment(\.dynamicTypeSize) private var size

    var body: some View {
        let layout = size > .large
            ? AnyLayout(FlowLayout(spacing: spacing, lineSpacing: lineSpacing))
            : AnyLayout(HStackLayout(spacing: spacing))
        layout { content }
    }
}

/// Views side by side, or one under the other at accessibility sizes,
/// where two full-size buttons or three figures no longer fit beside
/// each other and their words broke mid-word.
struct StackingRow<Content: View>: View {
    var spacing: CGFloat = 8
    @ViewBuilder var content: Content
    @Environment(\.dynamicTypeSize) private var size

    var body: some View {
        let layout = size.isAccessibilitySize
            ? AnyLayout(VStackLayout(spacing: spacing))
            : AnyLayout(HStackLayout(spacing: spacing))
        layout { content }
    }
}

/// See `View.lineLimit(_:larger:)`.
struct GrowingLineLimit: ViewModifier {
    var lines: Int
    var larger: Int
    @Environment(\.dynamicTypeSize) private var size

    func body(content: Content) -> some View {
        content.lineLimit(size > .large ? max(lines, larger) : lines)
    }
}

extension View {
    /// `lines` at the default text size and `larger` above it, so a title
    /// that fit one line at the default keeps about as many words on
    /// screen when its text grows.
    func lineLimit(_ lines: Int, larger: Int) -> some View {
        modifier(GrowingLineLimit(lines: lines, larger: larger))
    }
}
