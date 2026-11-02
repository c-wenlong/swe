// screenrec OUT.mov STOPFILE: record the main display to OUT.mov, H.264 at
// 30 fps, until STOPFILE exists. Prints "recording WxH" once it has started.
//
// Built and run inside the demo VM by scripts/demo_vm.sh. `screencapture -v`
// stops only on a keypress or its time limit and leaves no file when
// interrupted, so a scripted take records with ScreenCaptureKit instead.
// It needs Screen Recording for the app that runs it (Terminal, in the VM).
import AppKit
import Foundation
import ScreenCaptureKit

// The delegate may be told before anyone waits, so it signals a semaphore
// rather than resuming a continuation that might not exist yet. `error` is
// written before the signal and read after the wait, so the semaphore
// orders the two.
final class Finish: NSObject, SCRecordingOutputDelegate, @unchecked Sendable {
    private let done = DispatchSemaphore(value: 0)
    private(set) var error: Error?

    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) { done.signal() }

    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) {
        self.error = error
        done.signal()
    }

    /// Waits off the main thread, which AppKit and the stream need.
    func wait(seconds: Double) async -> Bool {
        await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                continuation.resume(returning: self.done.wait(timeout: .now() + seconds) == .success)
            }
        }
    }
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write("screenrec: \(message)\n".data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count == 3 else { fail("usage: screenrec OUT.mov STOPFILE") }
let output = URL(fileURLWithPath: args[1])
let stopFile = args[2]
let finish = Finish()

Task { @MainActor in
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else { fail("no display to record") }
        let config = SCStreamConfiguration()
        config.width = display.width
        config.height = display.height
        config.minimumFrameInterval = CMTime(value: 1, timescale: 30)
        config.showsCursor = true
        let stream = SCStream(filter: SCContentFilter(display: display, excludingWindows: []),
                              configuration: config, delegate: nil)
        let recordingConfig = SCRecordingOutputConfiguration()
        recordingConfig.outputURL = output
        recordingConfig.outputFileType = .mov
        recordingConfig.videoCodecType = .h264
        try stream.addRecordingOutput(SCRecordingOutput(configuration: recordingConfig, delegate: finish))
        try await stream.startCapture()
        print("recording \(display.width)x\(display.height)")
        fflush(stdout)
        while !FileManager.default.fileExists(atPath: stopFile) {
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        try await stream.stopCapture()
        // The delegate is told once the file is closed.
        guard await finish.wait(seconds: 20) else { fail("the recording did not finish") }
        if let error = finish.error { throw error }
        print("saved \(output.path)")
        exit(0)
    } catch {
        fail("\(error)")
    }
}

// ScreenCaptureKit puts its recording indicator in the menu bar, which AppKit
// only allows from a running app's main thread.
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
app.run()
