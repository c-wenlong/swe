import SWECore
import SwiftUI

/// The Edit tab's targets strip above the open chapter: which harnesses
/// receive it, why, and a chip per harness to change that without going
/// to Compile. It reads the rules through `ChapterReceipts` (the same
/// lookups the Compile tab uses) and writes through the model's
/// `setIncluded` / `setReceipt`, so both tabs share one save queue and a
/// change in either shows in the other after the rebuild.
struct ChapterTargetsStrip: View {
    var name: String
    var check: InstructionsCheck

    @Environment(AppModel.self) private var app
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The last refusal and the chapter it was about: the strip keeps its
    /// state across selections, and a write can finish after the user
    /// opens another chapter, so it only shows for its own chapter.
    @State private var failure: (chapter: String, message: String)?
    private var error: String? { failure?.chapter == name ? failure?.message : nil }
    /// The harnesses (and "" for the index checkbox) with a write in
    /// flight, so a second click can't compute from the stale state.
    @State private var pending: Set<String> = []

    var body: some View {
        let chapters = app.chapters
        // A deleted draft (no file, no index line) has nothing to target:
        // turning it on would list a file the build can't find.
        if case .loaded(let report) = app.agentsMd.check,
           chapters.files.contains(name) || check.compileState(of: name) != .unlisted {
            let receipts = ChapterReceipts(name: name, check: check,
                                           harnesses: report.instructionHarnessNames,
                                           tree: chapters.tree)
            let blocked = chapters.targetsParseProblem
            Strip {
                // Title and why on one line, the index checkbox at its end;
                // the chips get the strip's full width below so a narrow
                // window wraps them into as few rows as it can.
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text("Targets").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    Text(receipts.summary)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    includeToggle(receipts.state)
                }
                FlowLayout(spacing: 6, lineSpacing: 6) {
                    ForEach(receipts.receipts) { receipt in
                        chip(receipt, disabled: blocked != nil)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if let folder = receipts.folderSummary {
                    Label(folder, systemImage: "folder")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let problem = error ?? blocked {
                    Label(problem, systemImage: "exclamationmark.triangle")
                        .font(Theme.caption).foregroundStyle(Theme.dangerText)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .animation(reduceMotion ? nil : .easeOut(duration: 0.15), value: receipts)
        }
    }

    /// The index checkbox: whether the chapter compiles at all. Same
    /// write as Compile's All-harnesses checkbox.
    private func includeToggle(_ state: CompileState) -> some View {
        let on = state == .on
        return HStack(spacing: 6) {
            CompileView.TriBox(tri: on ? .all : .none) { toggleIncluded(!on) }
                .disabled(!pending.isEmpty)
            Text("In AGENTS.md").font(Theme.bodySmall)
                .foregroundStyle(on ? Theme.textPrimary : Theme.textMuted)
                .onTapGesture { if pending.isEmpty { toggleIncluded(!on) } }
        }
        .fixedSize()
        .hoverTip(on ? "Uncheck to comment its line out of index.txt; its rule stays"
                     : "Check to list it in index.txt",
                  replacesLabel: false)
    }

    /// One harness: its logo and name, bright on a filled chip when it
    /// gets the chapter, faded when it skips. Clicking flips that receipt,
    /// the same write as Compile's per-harness checkbox.
    private func chip(_ receipt: ChapterReceipts.Receipt, disabled: Bool) -> some View {
        let gets = receipt.gets
        let busy = pending.contains(receipt.harness)
        return Button { toggle(receipt.harness, want: !gets) } label: {
            HStack(spacing: 5) {
                CompileView.markIcon(receipt.harness, size: 12)
                    .opacity(gets ? 1 : 0.4)
                Text(HarnessMark(receipt.harness).name).font(Theme.bodySmall)
                    .foregroundStyle(gets ? Theme.textPrimary : Theme.textMuted)
                    .strikethrough(!gets, color: Theme.textMuted)
                if busy { ProgressView().controlSize(.mini) }
            }
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(gets ? Theme.navActive : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                .strokeBorder(gets ? .clear : Theme.border, lineWidth: 1))
            .contentShape(.rect)
        }
        .plainButton()
        .disabled(disabled || !pending.isEmpty)
        .accessibilityLabel(HarnessMark(receipt.harness).name)
        .accessibilityValue(gets ? "Gets this chapter" : "Skips this chapter")
        .hoverTip("\(HarnessMark(receipt.harness).name) \(gets ? "gets" : "skips") this chapter; click to \(gets ? "drop it" : "add it")",
                  replacesLabel: false)
    }

    private func toggle(_ harness: String, want: Bool) {
        pending.insert(harness)
        let swe = app.swe
        let chapters = app.chapters
        let chapter = name
        Task {
            report(await chapters.setReceipt(name: chapter, harness: harness,
                                             want: want, swe: swe), for: chapter)
            pending.remove(harness)
        }
    }

    private func toggleIncluded(_ on: Bool) {
        pending.insert("")
        let swe = app.swe
        let chapters = app.chapters
        let chapter = name
        Task {
            report(await chapters.setIncluded(name: chapter, on: on, swe: swe),
                   for: chapter)
            pending.remove("")
        }
    }

    /// A write's outcome for the chapter it changed: a refusal is kept
    /// against that chapter, a success clears only that chapter's refusal.
    private func report(_ problem: String?, for chapter: String) {
        if let problem {
            failure = (chapter, problem)
        } else if failure?.chapter == chapter {
            failure = nil
        }
    }
}
