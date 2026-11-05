import Foundation
import SWECore
import Testing
@testable import SWEApp

/// The Inference key editor's Save: test once when worth it, ask before
/// saving a rejected key, and never save twice or after the sheet closed.
@Suite @MainActor struct KeySaveFlowTests {
    private struct DiskFull: LocalizedError {
        var errorDescription: String? { "The disk is full" }
    }

    @MainActor final class Log {
        var tests = 0
        var saves: [APIKeyCheckResult?] = []
        var heard: [APIKeyCheckResult?] = []
        var answer: APIKeyCheckResult? = .works
        var saveError: Error?

        var flow: KeySaveFlow {
            KeySaveFlow(
                test: { self.tests += 1; return self.answer },
                save: { verdict in
                    if let error = self.saveError { throw error }
                    self.saves.append(verdict)
                })
        }

        func run(value: String = "sk-new", testable: Bool = true,
                 current: APIKeyCheckResult? = nil, saveAnyway: Bool = false) async -> KeySaveFlow.Outcome {
            await flow.run(value: value, testable: testable, current: current,
                           saveAnyway: saveAnyway) { self.heard.append($0) }
        }
    }

    @Test func aNewKeyIsTestedThenSavedWithItsVerdict() async {
        let log = Log()
        #expect(await log.run() == .saved)
        #expect(log.tests == 1)
        #expect(log.saves == [.works])
        #expect(log.heard == [.works])
    }

    @Test func aVerdictThatStillMatchesIsNotTestedAgain() async {
        let log = Log()
        #expect(await log.run(current: .noCredit) == .saved)
        #expect(log.tests == 0)
        #expect(log.saves == [.noCredit])
        #expect(log.heard.isEmpty)
    }

    @Test func anUntestableEditSavesWithoutAProviderCall() async {
        let log = Log()
        #expect(await log.run(testable: false) == .saved)
        #expect(log.tests == 0)
        #expect(log.saves == [nil])
    }

    /// A rejected key stops once, saving nothing; the next tap, with
    /// "Save anyway" showing, saves it with the rejection recorded.
    @Test func aRejectedKeyAsksFirstThenSavesAnyway() async {
        let log = Log()
        log.answer = .rejected(status: 401, message: "bad key")
        #expect(await log.run() == .confirmAnyway)
        #expect(log.saves.isEmpty)
        #expect(await log.run(current: log.heard.last!, saveAnyway: true) == .saved)
        #expect(log.tests == 1, "the second tap reuses the verdict")
        #expect(log.saves == [.rejected(status: 401, message: "bad key")])
    }

    /// A key the check could not judge is saved as is: only a refusal asks.
    @Test func anInconclusiveCheckStillSaves() async {
        let log = Log()
        log.answer = .couldNotCheck("timeout")
        #expect(await log.run() == .saved)
        #expect(log.saves == [.couldNotCheck("timeout")])
    }

    /// An empty value (a provider move keeping its key) never carries a
    /// verdict about a value that isn't there.
    @Test func anEmptyValueSavesWithNoVerdict() async {
        let log = Log()
        #expect(await log.run(value: "", testable: false, current: .works) == .saved)
        #expect(log.saves == [nil])
    }

    @Test func aFailedWriteReportsItsErrorAndKeepsTheSheet() async {
        let log = Log()
        log.saveError = DiskFull()
        #expect(await log.run() == .failed("The disk is full"))
        #expect(log.saves.isEmpty)
    }

    /// Closing the sheet mid-test cancels the task: nothing is saved.
    @Test func aCancelledSaveWritesNothing() async {
        let log = Log()
        let flow = KeySaveFlow(
            test: {
                withUnsafeCurrentTask { $0?.cancel() }
                return .works
            },
            save: { log.saves.append($0) })
        let outcome = await flow.run(value: "sk", testable: true, current: nil, saveAnyway: false) { _ in }
        #expect(outcome == .cancelled)
        #expect(log.saves.isEmpty)
    }
}
