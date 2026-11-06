import Foundation
import Testing

/// The helpers other tests lean on to stay off the real home.
@Suite struct TestSupportTests {
    @Test(.timeLimit(.minutes(1))) func aScratchSuiteIsWrittenUnderTheTempFolder() async {
        let defaults = TestDefaults("support")
        defaults.set(42, forKey: "answer")
        #expect(UserDefaults(suiteName: defaults.suite)?.integer(forKey: "answer") == 42)
        // The plist lands in this process's temp folder, not beside the
        // user's own preferences. cfprefsd writes it in its own time (a
        // flush doesn't wait for the file under load), so wait for it.
        let file = URL(filePath: defaults.suite + ".plist")
        #expect(file.deletingLastPathComponent().path == TestDefaults.folder.path)
        let appeared = await fileAppears(file.lastPathComponent, in: TestDefaults.folder)
        // Held to here: its deinit deletes the plist being waited for.
        withExtendedLifetime(defaults) {}
        #expect(appeared)
        let preferences = FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/Preferences/\(file.lastPathComponent)")
        #expect(!FileManager.default.fileExists(atPath: preferences.path))
    }

    /// A wait that ignores cancellation still ends at the timeout: a
    /// task group used to sit on it until it finished, which was never.
    @MainActor
    @Test(.timeLimit(.minutes(1))) func withinTimeoutDoesNotWaitForAWaitThatIgnoresCancellation() async {
        let never = HoldGate()
        let stuck = Task { try? await never.pass() }
        // A task's value can't be cancelled from outside the task.
        let answered = await withinTimeout(.milliseconds(50)) {
            await stuck.value
            return true
        }
        #expect(!answered)
        never.open()
    }

    @MainActor
    @Test(.timeLimit(.minutes(1))) func withinTimeoutAnswersWithTheWaitWhenItFinishesFirst() async {
        #expect(await withinTimeout { true })
        #expect(await withinTimeout { false } == false)
    }

    /// Cancelling the caller answers false at once, not at the timeout,
    /// even while its wait ignores cancellation.
    @MainActor
    @Test(.timeLimit(.minutes(1))) func withinTimeoutAnswersFalseWhenTheCallerIsCancelled() async {
        let never = HoldGate()
        let stuck = Task { try? await never.pass() }
        let entered = CountGate()
        let caller = Task { @MainActor in
            await withinTimeout(.seconds(3600)) {
                await entered.bump()
                await stuck.value
                return true
            }
        }
        #expect(await entered.reach(1))
        caller.cancel()
        #expect(await caller.value == false)
        never.open()
    }

    /// A child that outlives the bound is killed along with what it
    /// started, and the wait reports it as an issue.
    @Test(.timeLimit(.minutes(1))) func waitForExitKillsAChildTreeThatOutlivesTheTimeout() async throws {
        let temp = try TempDir()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        // Written whole, then moved into place, so the file never shows half a pid.
        let file = temp.path("grandchild").path
        process.arguments = ["-c", "sleep 30 & echo $! > '\(file).tmp' && mv '\(file).tmp' '\(file)'; wait"]
        try process.run()
        // The grandchild exists once its pid is written.
        #expect(await fileAppears("grandchild", in: temp.url))
        let text = (try? String(contentsOf: temp.path("grandchild"), encoding: .utf8)) ?? ""
        let grandchild = try #require(pid_t(text.trimmingCharacters(in: .whitespacesAndNewlines)))
        withKnownIssue("the child is still running at the timeout") {
            waitForExit(process, timeout: .milliseconds(200))
        }
        #expect(!process.isRunning)
        #expect(await processEnds(grandchild))
    }

    @Test(.timeLimit(.minutes(1))) func waitForExitReturnsAtOnceWhenTheChildEnds() throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/true")
        try process.run()
        waitForExit(process)
        #expect(process.terminationStatus == 0)
    }
}
