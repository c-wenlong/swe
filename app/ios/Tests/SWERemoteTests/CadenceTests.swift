import XCTest
@testable import SWERemote

/// Console output reaches the main queue in batches, and upload progress
/// at most ten times a second.
final class CadenceTests: XCTestCase {
    /// Collects the drains a batcher schedules instead of running them.
    private final class Queue: @unchecked Sendable {
        let lock = NSLock()
        var blocks: [@Sendable () -> Void] = []
        func add(_ block: @escaping @Sendable () -> Void) { lock.withLock { blocks.append(block) } }
        func runAll() {
            let run = lock.withLock { () -> [@Sendable () -> Void] in defer { blocks = [] }; return blocks }
            run.forEach { $0() }
        }
    }

    @MainActor
    func testABurstOfOutputIsOneHopAndOneFeed() {
        let queue = Queue()
        var delivered: [[SSHShell.Event]] = []
        let batcher = ConsoleEventBatcher(schedule: queue.add) { delivered.append($0) }
        // 500 chunks from NIO's thread before the main queue gets round.
        DispatchQueue.concurrentPerform(iterations: 1) { _ in
            for i in 0..<500 { batcher.push(.output([UInt8(i % 256)])) }
        }
        XCTAssertEqual(queue.blocks.count, 1, "one main-queue hop for the burst, not one per chunk")
        queue.runAll()
        XCTAssertEqual(delivered.count, 1)
        guard case .output(let bytes)? = delivered.first?.first, delivered.first?.count == 1 else {
            return XCTFail("one joined output: \(delivered)")
        }
        XCTAssertEqual(bytes, (0..<500).map { UInt8($0 % 256) }, "in order, nothing lost")
    }

    @MainActor
    func testACloseStaysAfterTheOutputBeforeIt() {
        let queue = Queue()
        var delivered: [SSHShell.Event] = []
        let batcher = ConsoleEventBatcher(schedule: queue.add) { delivered += $0 }
        batcher.push(.output(Array("bye".utf8)))
        batcher.push(.output(Array("\n".utf8)))
        batcher.push(.closed(nil))
        queue.runAll()
        XCTAssertEqual(delivered.count, 2)
        guard case .output(let bytes) = delivered[0], case .closed(nil) = delivered[1] else {
            return XCTFail("\(delivered)")
        }
        XCTAssertEqual(bytes, Array("bye\n".utf8))
        // Output after a drain schedules the next one.
        batcher.push(.output([1]))
        XCTAssertEqual(queue.blocks.count, 1)
    }

    func testUploadProgressIsTenASecondAndAlwaysTheEnd() {
        var throttle = ProgressThrottle()
        // A fast upload: 1,000 callbacks over 2 s.
        var admitted: [Double] = []
        for i in 1...1000 {
            let fraction = Double(i) / 1000
            if throttle.admit(fraction, at: Double(i) * 0.002) { admitted.append(fraction) }
        }
        XCTAssertLessThanOrEqual(admitted.count, 22, "about ten a second over two seconds")
        XCTAssertEqual(admitted.first, 0.001, "the first report goes at once")
        XCTAssertEqual(admitted.last, 1, "the end always goes")
        XCTAssertEqual(admitted, admitted.sorted())
        // Never backwards, even after the interval.
        XCTAssertFalse(throttle.admit(0.5, at: 100))
    }
}
