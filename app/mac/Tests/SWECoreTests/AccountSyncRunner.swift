import Foundation
import Synchronization
@testable import SWECore

/// For `AccountSyncTests`: `config set` can fail or be held at `configGate`
/// until the test opens it; every `init` call answers with the captured
/// fixture.
final class AccountSyncRunner: CommandRunning, Sendable {
    let configGate = HoldGate()
    private let recorded = Mutex<[[String]]>([])
    private let fixture: Data
    private let holdConfig: Bool
    private let configFails: Bool

    init(fixture: Data, holdConfig: Bool, configFails: Bool) {
        self.fixture = fixture
        self.holdConfig = holdConfig
        self.configFails = configFails
    }

    var calls: [[String]] { recorded.withLock { $0 } }

    func run(_ arguments: [String]) async throws -> CommandResult {
        recorded.withLock { $0.append(arguments) }
        if arguments.first == "config" {
            if holdConfig { try await configGate.pass() }
            if configFails {
                return .init(status: 1, stdout: Data(), stderr: "config.json is read-only")
            }
            return .init(status: 0, stdout: Data(), stderr: "")
        }
        return .init(status: 0, stdout: fixture, stderr: "")
    }
}
