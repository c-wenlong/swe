import ImageIO
import UIKit
import XCTest
@testable import SWERemote

final class AttachmentRulesTests: XCTestCase {
    func testTheHubsTypesAndSizeCap() {
        XCTAssertNil(AttachmentRules.refusal(name: "shot.png", size: 1024))
        XCTAssertNil(AttachmentRules.refusal(name: "notes.MD", size: 10))
        XCTAssertNil(AttachmentRules.refusal(name: "spec.pdf", size: AttachmentRules.maxBytes))
        XCTAssertEqual(AttachmentRules.refusal(name: "report.docx", size: 10),
                       "report.docx can't be attached: images, PDFs and text files only.")
        XCTAssertNotNil(AttachmentRules.refusal(name: "Makefile", size: 10))
        let big = AttachmentRules.refusal(name: "scan.pdf", size: AttachmentRules.maxBytes + 1)
        XCTAssertEqual(big, "scan.pdf is too big (21 MB): files can be up to 20 MB.")
        XCTAssertEqual(AttachmentRules.refusal(name: "empty.txt", size: 0), "empty.txt is empty.")
    }

    func testImagesModelsCantReadAreConverted() {
        XCTAssertTrue(AttachmentRules.needsConversion("IMG_0001.HEIC"))
        XCTAssertTrue(AttachmentRules.needsConversion("scan.tiff"))
        XCTAssertFalse(AttachmentRules.needsConversion("shot.png"))
        XCTAssertFalse(AttachmentRules.needsConversion("photo.jpeg"))
        XCTAssertFalse(AttachmentRules.needsConversion("spec.pdf"))
        XCTAssertFalse(AttachmentRules.needsConversion("README"))
        XCTAssertEqual(AttachmentRules.jpegName("IMG_0001.HEIC"), "IMG_0001.jpg")
    }

    func testDisplayNameDropsTheHubsPrefix() {
        XCTAssertEqual(AttachmentRules.displayName(ref: "1730000000-a1b2c3-photo-1.jpg"), "photo-1.jpg")
        XCTAssertEqual(AttachmentRules.displayName(ref: "/x/remote/inbox/ab/1730000000-a1b2c3-notes.md"), "notes.md")
        // Not one of the hub's refs: shown as it is.
        XCTAssertEqual(AttachmentRules.displayName(ref: "my-file-name.txt"), "my-file-name.txt")
        XCTAssertEqual(AttachmentRules.displayName(ref: "12-xyzxyz-a.txt"), "12-xyzxyz-a.txt")
    }

    func testAnEchoSplitsIntoFilesAndText() {
        let echo = "@/Users/k/.swe/data/remote/inbox/0123456789abcdef01234567/1730000000-a1b2c3-photo-1.jpg "
            + "@/Users/k/.swe/data/remote/inbox/0123456789abcdef01234567/1730000001-d4e5f6-log.txt why does this fail?"
        let split = AttachmentRules.splitEcho(echo)
        XCTAssertEqual(split.names, ["photo-1.jpg", "log.txt"])
        XCTAssertEqual(split.text, "why does this fail?")
        // A typed @-mention of a project file is the user's own text.
        XCTAssertEqual(AttachmentRules.splitEcho("@src/main.swift fix this").names, [])
        XCTAssertEqual(AttachmentRules.splitEcho("@src/main.swift fix this").text, "@src/main.swift fix this")
        XCTAssertEqual(AttachmentRules.splitEcho("plain").text, "plain")
        // A typed mention that quotes an inbox path further on stays text.
        let quoted = "@src/main.swift compare with /h/remote/inbox/0123456789abcdef01234567/1730000000-a1b2c3-shot.png explain"
        XCTAssertEqual(AttachmentRules.splitEcho(quoted).names, [])
        XCTAssertEqual(AttachmentRules.splitEcho(quoted).text, quoted)
        // A home folder with a space in it.
        let spaced = AttachmentRules.splitEcho("@/Volumes/Work Drive/k/.swe/data/remote/inbox/0123456789abcdef01234567/1730000000-a1b2c3-a.png what")
        XCTAssertEqual(spaced.names, ["a.png"])
        XCTAssertEqual(spaced.text, "what")
    }

    func testAStagedAnswerDecodes() throws {
        let body = #"{"ref":"1730000000-a1b2c3-x.png","name":"x.png","path":"/p","size":12,"queued":true}"#
        let staged = try Hub.decoder.decode(StagedAttachment.self, from: Data(body.utf8))
        XCTAssertEqual(staged, StagedAttachment(ref: "1730000000-a1b2c3-x.png", name: "x.png", size: 12))
    }

    func testUploadRefusalsAreWorded() {
        let unknown = HubClient.error(status: 422, body: Data(#"{"error":{"code":"bad_attachment","message":"unknown attachment 1-abcdef-x.png"}}"#.utf8))
        XCTAssertEqual(unknown.message, "The hub no longer has an attached file. Attach it again.")
        let type = HubClient.error(status: 422, body: Data(#"{"error":{"code":"bad_attachment","message":"unsupported file type .exe"}}"#.utf8))
        XCTAssertEqual(type.message, "Unsupported file type .exe")
        let big = HubClient.error(status: 413, body: Data(#"{"error":{"code":"too_large","message":"attachment exceeds 20 MB"}}"#.utf8))
        XCTAssertEqual(ComposerAttachments.failure(big), "Over the hub's 20 MB limit")
    }
}

final class PhotoPrepTests: XCTestCase {
    private func png(width: Int, height: Int) -> Data {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: CGSize(width: width, height: height), format: format).pngData { ctx in
            UIColor.systemTeal.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
        }
    }

    private func pixels(_ data: Data) -> (Int, Int, String?) {
        let source = CGImageSourceCreateWithData(data as CFData, nil)!
        let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as! [CFString: Any]
        return (props[kCGImagePropertyPixelWidth] as! Int, props[kCGImagePropertyPixelHeight] as! Int,
                CGImageSourceGetType(source) as String?)
    }

    func testABigPhotoBecomesAJpegCappedOnItsLongEdge() throws {
        let jpeg = try XCTUnwrap(PhotoPrep.jpeg(from: png(width: 4000, height: 1000)))
        let (w, h, type) = pixels(jpeg)
        XCTAssertEqual(w, 2048)
        XCTAssertEqual(h, 512)
        XCTAssertEqual(type, "public.jpeg")
    }

    func testASmallPhotoKeepsItsSize() throws {
        let jpeg = try XCTUnwrap(PhotoPrep.jpeg(from: png(width: 300, height: 200)))
        let (w, h, _) = pixels(jpeg)
        XCTAssertEqual(w, 300)
        XCTAssertEqual(h, 200)
    }

    func testNotAnImage() {
        XCTAssertNil(PhotoPrep.jpeg(from: Data("hello".utf8)))
    }
}

/// Holds uploads until a test lets them finish.
@MainActor
private final class Gate {
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var opened = false

    func wait() async {
        if opened { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func open() {
        opened = true
        waiters.forEach { $0.resume() }
        waiters = []
    }
}

private struct Refused: Error {}

@MainActor
final class ComposerAttachmentsTests: XCTestCase {
    private var uploads: [(String, Int)] = []
    /// The files uploads streamed from.
    private var sentFrom: [URL] = []
    private var failNext = false

    private func model(gate: Gate? = nil) -> ComposerAttachments {
        let files = ComposerAttachments()
        files.upload = { [weak self] name, file, progress in
            // The body streams from the spooled copy; read it as the hub would.
            let data = (try? Data(contentsOf: file)) ?? Data()
            await MainActor.run {
                self?.uploads.append((name, data.count))
                self?.sentFrom.append(file)
            }
            progress(0.5)
            await gate?.wait()
            let fail = await MainActor.run { () -> Bool in
                defer { self?.failNext = false }
                return self?.failNext ?? false
            }
            if fail { throw HubError.network(Refused()) }
            return StagedAttachment(ref: "1730000000-a1b2c3-\(name)", name: name, size: data.count)
        }
        files.convert = { $0 }
        return files
    }

    func testSendWaitsWhileAnUploadIsInFlight() async {
        let gate = Gate()
        let files = model(gate: gate)
        let task = files.addFile(name: "log.txt", data: Data("boom".utf8))
        XCTAssertEqual(files.items.first?.state, .preparing)
        XCTAssertTrue(files.blocksSend)
        await uploadStarted(files)
        XCTAssertEqual(files.items.first?.state, .uploading(0))
        XCTAssertTrue(files.blocksSend)
        XCTAssertEqual(files.refs, [])
        gate.open()
        await task?.value
        XCTAssertFalse(files.blocksSend)
        XCTAssertEqual(files.refs, ["1730000000-a1b2c3-log.txt"])
        XCTAssertEqual(files.items.first?.size, 4)
        XCTAssertEqual(uploads.map(\.0), ["log.txt"])
    }

    /// Waits for the first chip to leave "Preparing" (bounded, so a
    /// regression fails the test instead of hanging the run).
    private func uploadStarted(_ files: ComposerAttachments) async {
        for _ in 0..<100_000 where files.items.first?.state == .preparing { await Task.yield() }
    }

    func testProgressOnlyMovesForward() async {
        let gate = Gate()
        // The held upload is let go at the end, so no task outlives the test.
        defer { gate.open() }
        let files = model(gate: gate)
        files.addFile(name: "a.txt", data: Data("a".utf8))
        await uploadStarted(files)
        let id = files.items[0].id
        files.progressed(id, 0.4)
        XCTAssertEqual(files.items[0].state, .uploading(0.4))
        files.progressed(id, 0.2)
        XCTAssertEqual(files.items[0].state, .uploading(0.4))
        XCTAssertEqual(AttachmentChip.detail(files.items[0]), "Uploading 40%")
    }

    func testAFailedUploadBlocksSendUntilRetriedOrRemoved() async {
        let files = model()
        failNext = true
        await files.addFile(name: "a.txt", data: Data("a".utf8))?.value
        XCTAssertEqual(files.items[0].state, .failed("Can't reach the hub"))
        XCTAssertTrue(files.blocksSend)
        XCTAssertEqual(AttachmentChip.detail(files.items[0]), "Can't reach the hub. Tap to retry")
        await files.retry(files.items[0].id)?.value
        XCTAssertEqual(files.refs, ["1730000000-a1b2c3-a.txt"])
        XCTAssertFalse(files.blocksSend)

        failNext = true
        await files.addFile(name: "b.txt", data: Data("b".utf8))?.value
        XCTAssertTrue(files.blocksSend)
        files.remove(files.items[1].id)
        XCTAssertFalse(files.blocksSend)
        XCTAssertEqual(files.items.map(\.name), ["a.txt"])
    }

    func testRemovingAnUploadStopsIt() async {
        let gate = Gate()
        let files = model(gate: gate)
        let task = files.addFile(name: "a.txt", data: Data("a".utf8))
        files.remove(files.items[0].id)
        gate.open()
        await task?.value
        XCTAssertTrue(files.items.isEmpty)
        XCTAssertFalse(files.blocksSend)
    }

    func testTheHubsCapsAreCheckedBeforeUploading() async {
        let files = model()
        XCTAssertNil(files.addFile(name: "report.docx", data: Data("x".utf8)), "refused from its name, no chip")
        XCTAssertTrue(files.items.isEmpty)
        XCTAssertTrue(files.notice?.hasPrefix("report.docx can't be attached") == true)
        await files.addFile(name: "huge.pdf", data: Data(count: AttachmentRules.maxBytes + 1))?.value
        XCTAssertTrue(files.items.isEmpty)
        XCTAssertTrue(files.notice?.hasPrefix("huge.pdf is too big") == true)
        XCTAssertTrue(uploads.isEmpty)
        // The next pick clears the notice.
        files.addFile(name: "ok.txt", data: Data("x".utf8))
        XCTAssertNil(files.notice)
    }

    func testAMessageTakesUpToTenFiles() {
        let gate = Gate()
        defer { gate.open() }
        let files = model(gate: gate)
        for i in 0..<AttachmentRules.maxPerMessage { files.addFile(name: "\(i).txt", data: Data("x".utf8)) }
        XCTAssertEqual(files.room, 0)
        XCTAssertNil(files.addFile(name: "more.txt", data: Data("x".utf8)))
        XCTAssertEqual(files.notice, "Up to 10 files per message.")
        XCTAssertEqual(files.items.count, AttachmentRules.maxPerMessage)
    }

    func testAPhotoIsPreparedThenUploadedAsJpeg() async {
        let files = model()
        let task = files.addPhoto { Data("pixels".utf8) }
        XCTAssertEqual(files.items.first?.state, .preparing)
        XCTAssertEqual(files.items.first?.name, "photo-1.jpg")
        XCTAssertTrue(files.blocksSend)
        await task?.value
        XCTAssertEqual(files.refs, ["1730000000-a1b2c3-photo-1.jpg"])
        XCTAssertEqual(files.items.first?.size, 6)
        files.addPhoto { Data("more".utf8) }
        XCTAssertEqual(files.items.last?.name, "photo-2.jpg")
    }

    func testAPhotoThatCantBeReadLeavesANotice() async {
        let files = model()
        files.convert = { _ in nil }
        await files.addPhoto { Data("x".utf8) }?.value
        XCTAssertTrue(files.items.isEmpty)
        XCTAssertEqual(files.notice, "photo-1.jpg couldn't be read as an image.")
    }

    func testAHeicFileIsConvertedAndRenamed() async {
        let files = model()
        await files.addFile(name: "IMG_0001.HEIC", data: Data("heic".utf8))?.value
        XCTAssertEqual(files.items.first?.name, "IMG_0001.jpg")
        XCTAssertEqual(uploads.map(\.0), ["IMG_0001.jpg"])
    }

    func testFilesFromThePickerAreSizedBeforeReading() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let big = dir.appendingPathComponent("dump.log")
        FileManager.default.createFile(atPath: big.path, contents: nil)
        let handle = try FileHandle(forWritingTo: big)
        try handle.truncate(atOffset: UInt64(AttachmentRules.maxBytes + 1))
        try handle.close()
        XCTAssertNil(ComposerAttachments.spool(big, name: "dump.log").file, "over the cap: not even copied")

        let files = model()
        await files.addFile(at: big)?.value
        XCTAssertTrue(files.notice?.hasPrefix("dump.log is too big") == true, files.notice ?? "")
        XCTAssertTrue(uploads.isEmpty)

        let small = dir.appendingPathComponent("notes.md")
        try Data("# hi".utf8).write(to: small)
        await files.addFile(at: small)?.value
        XCTAssertNil(files.notice)
        XCTAssertEqual(files.items.map(\.name), ["notes.md"])

        await files.addFile(at: dir.appendingPathComponent("missing.txt"))?.value
        XCTAssertEqual(files.notice, "missing.txt couldn't be read.")
    }

    func testAPickStillLoadingHoldsSendAndASendDropsIt() async {
        let gate = Gate()
        let files = model()
        let task = files.addPhoto {
            await gate.wait()
            return Data("late".utf8)
        }
        XCTAssertEqual(files.items.first?.state, .preparing)
        XCTAssertTrue(files.blocksSend, "a pick still being read holds Send")
        XCTAssertEqual(files.room, AttachmentRules.maxPerMessage - 1)
        files.clear()
        gate.open()
        await task?.value
        let loaded = files.items.isEmpty
        XCTAssertTrue(loaded, "a read that finishes after a send doesn't land in the next draft")
        XCTAssertTrue(uploads.isEmpty)
    }

    func testDetachDropsACachedChatsFiles() async {
        let app = AppModel(hub: nil)
        let chat = app.chat("claude:1")
        chat.attachments.upload = { name, _, _ in StagedAttachment(ref: "1730000000-a1b2c3-\(name)") }
        await chat.attachments.addFile(name: "a.txt", data: Data("a".utf8))?.value
        XCTAssertEqual(chat.attachments.refs.count, 1)
        app.patch("claude:1", ownership: "observed")
        XCTAssertTrue(chat.attachments.isEmpty)
    }

    func testImagesPrepareOneAtATime() async {
        let gate = Gate()
        let files = model()
        var loads = 0
        let first = files.addPhoto {
            await MainActor.run { loads += 1 }
            await gate.wait()
            return Data("a".utf8)
        }
        let second = files.addPhoto {
            await MainActor.run { loads += 1 }
            return Data("b".utf8)
        }
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(loads, 1, "the second image waits for the first")
        // Removed while waiting its turn: it never loads.
        files.remove(files.items[1].id)
        gate.open()
        await first?.value
        await second?.value
        XCTAssertEqual(loads, 1)
        XCTAssertEqual(files.refs, ["1730000000-a1b2c3-photo-1.jpg"])
    }

    func testAnUploadDoesNotHoldTheNextImagesTurn() async {
        let gate = Gate()
        let files = model(gate: gate)
        let first = files.addPhoto { Data("a".utf8) }
        let second = files.addPhoto { Data("b".utf8) }
        // Both reach their upload while the first upload is still held.
        for _ in 0..<500 where uploads.count < 2 { await Task.yield() }
        XCTAssertEqual(uploads.map(\.0).sorted(), ["photo-1.jpg", "photo-2.jpg"])
        gate.open()
        await first?.value
        await second?.value
        XCTAssertEqual(files.refs.count, 2)
    }

    private func exists(_ url: URL) -> Bool { FileManager.default.fileExists(atPath: url.path) }

    /// The file a load made, read from the test.
    private final class Made: @unchecked Sendable {
        private let lock = NSLock()
        private var _url: URL?
        var url: URL? { lock.withLock { _url } }
        func set(_ url: URL) { lock.withLock { _url = url } }
    }

    func testAPickedFileUploadsFromASpoolCopyThatGoesOnceSent() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("pick-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let picked = dir.appendingPathComponent("notes.md")
        try Data("# notes".utf8).write(to: picked)

        let files = model()
        await files.addFile(at: picked)?.value
        XCTAssertEqual(files.refs, ["1730000000-a1b2c3-notes.md"])
        XCTAssertEqual(uploads.first?.1, 7)
        let sent = try XCTUnwrap(sentFrom.first)
        XCTAssertEqual(sent.deletingLastPathComponent().standardizedFileURL,
                       AttachmentSpool.folder.standardizedFileURL, "streamed from the spool, not read into memory")
        XCTAssertFalse(exists(sent), "the copy goes once the hub has it")
        XCTAssertTrue(exists(picked), "the picked original is left alone")
    }

    func testAFailedUploadKeepsItsFileForRetryAndRemoveDeletesIt() async throws {
        let files = model()
        failNext = true
        await files.addFile(name: "a.txt", data: Data("a".utf8))?.value
        let kept = try XCTUnwrap(sentFrom.last)
        XCTAssertTrue(exists(kept), "Retry needs the bytes: they wait on disk, not in memory")
        await files.retry(files.items[0].id)?.value
        XCTAssertEqual(sentFrom.last, kept, "the retry sends the same file")
        XCTAssertFalse(exists(kept))

        failNext = true
        await files.addFile(name: "b.txt", data: Data("b".utf8))?.value
        let failed = try XCTUnwrap(sentFrom.last)
        XCTAssertTrue(exists(failed))
        files.remove(files.items[1].id)
        XCTAssertFalse(exists(failed), "dropping the chip deletes its file")

        failNext = true
        await files.addFile(name: "c.txt", data: Data("c".utf8))?.value
        let cleared = try XCTUnwrap(sentFrom.last)
        files.clear()
        XCTAssertFalse(exists(cleared), "a send or clear deletes the kept files")
    }

    func testAPhotoFromPhotosConvertsFromItsFile() async throws {
        let files = model()
        var convertedFrom: URL?
        files.convertFile = { url in
            convertedFrom = url
            return Data("jpeg".utf8)
        }
        let original = try AttachmentSpool.write(Data("original pixels".utf8))
        await files.addPhoto(file: { original })?.value
        XCTAssertEqual(convertedFrom, original)
        XCTAssertFalse(exists(original), "the original copy goes once the JPEG is made")
        XCTAssertEqual(uploads.first?.0, "photo-1.jpg")
        XCTAssertEqual(uploads.first?.1, 4)
        XCTAssertEqual(files.refs.count, 1)
    }

    func testARemovedPickNeverLeavesItsFileBehind() async throws {
        let gate = Gate()
        let files = model()
        let made = Made()
        let task = files.addPhoto(file: {
            // Photos copies the original into the spool as the load runs.
            let original = try AttachmentSpool.write(Data("pixels".utf8))
            made.set(original)
            await gate.wait()
            return original
        })
        // Let the load start, then send the message.
        for _ in 0..<100_000 where made.url == nil { await Task.yield() }
        files.clear()
        gate.open()
        await task?.value
        let original = try XCTUnwrap(made.url)
        XCTAssertFalse(exists(original), "a pick that lands after a clear deletes its copy")
        XCTAssertTrue(uploads.isEmpty)
    }

    func testOpeningAnotherChatNeverSweepsALiveRetryFile() throws {
        // The launch's sweep has run (the first composer does it).
        AttachmentSpool.sweepLeftovers()
        // A file kept for Retry, copied from an original last changed
        // long ago, so its date says nothing about how live it is.
        let kept = try AttachmentSpool.write(Data("pdf".utf8))
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(-30 * 24 * 3600)],
                                              ofItemAtPath: kept.path)
        _ = ComposerAttachments()
        AttachmentSpool.sweepLeftovers()
        XCTAssertTrue(exists(kept), "only an earlier run's files are swept, once per launch")
        AttachmentSpool.remove(kept)
    }

    func testClearingTheSpoolEmptiesIt() throws {
        let a = try AttachmentSpool.write(Data("a".utf8))
        let b = try AttachmentSpool.copy(a)
        AttachmentSpool.clear()
        XCTAssertFalse(exists(a))
        XCTAssertFalse(exists(b))
    }

    func testClearEmptiesTheComposer() {
        let gate = Gate()
        defer { gate.open() }
        let files = model(gate: gate)
        files.addFile(name: "a.txt", data: Data("a".utf8))
        files.clear()
        XCTAssertTrue(files.isEmpty)
        XCTAssertFalse(files.blocksSend)
    }
}

@MainActor
final class ChatAttachmentTests: XCTestCase {
    func testFilesGoWithTypedTextAndWaitWhileUploading() {
        XCTAssertEqual(ChatModel.composerAction(draft: "look", agentWorking: false, files: 1), .send)
        // The hub needs text with the files; stop stays out of the way.
        XCTAssertEqual(ChatModel.composerAction(draft: "", agentWorking: false, files: 1), .disabled)
        XCTAssertEqual(ChatModel.composerAction(draft: " ", agentWorking: true, files: 1), .disabled)
        XCTAssertEqual(ChatModel.composerAction(draft: "look", agentWorking: false, files: 1, filesBusy: true), .disabled)
        XCTAssertEqual(ChatModel.composerAction(draft: "", agentWorking: true, files: 1, filesBusy: true), .disabled)
        // No chips: unchanged.
        XCTAssertEqual(ChatModel.composerAction(draft: "", agentWorking: true), .stop)
        // An edit saves text only.
        XCTAssertEqual(ChatModel.composerAction(draft: "x", agentWorking: true, editing: true, files: 1, filesBusy: true), .send)
        XCTAssertEqual(ChatModel.composerAction(draft: "", agentWorking: true, editing: true, files: 1), .disabled)
    }

    func testAQueuedSendKeepsItsFilesInTheTray() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        let refs = ["1730000000-a1b2c3-photo-1.jpg"]
        chat.stage("what is this?", clientId: "c1", toTray: true, files: refs)
        XCTAssertEqual(chat.tray.first?.attachments, refs)
        chat.posted("c1", waiting: true)
        XCTAssertEqual(chat.tray.first?.attachments, refs)
        // The hub's list after reopening still says which files it holds.
        let merged = ChatModel.mergeQueue(local: [], hub: [WaitingSend(clientId: "c2", text: "t", attachments: [.string("r1"), .number(3)])],
                                          settled: [])
        XCTAssertEqual(merged.queued.first?.attachments, ["r1"])
        // A posting send the hub now lists keeps the phone's copy, files and all.
        let kept = ChatModel.mergeQueue(local: [QueuedSend(clientId: "c1", text: "t", phase: .posting, attachments: refs)],
                                        hub: [WaitingSend(clientId: "c1", text: "t")], settled: [])
        XCTAssertEqual(kept.queued.first, QueuedSend(clientId: "c1", text: "t", phase: .waiting, attachments: refs))
    }

    func testARestoredQueuedSendKeepsItsFilesWhenItFails() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.mergeQueue([WaitingSend(clientId: "c1", text: "look", attachments: [.string("1730000000-a1b2c3-a.png")])])
        chat.apply(env(#"{"type":"command.result","seq":50,"data":{"op":"message","ok":false,"note":"x","client_id":"c1"}}"#))
        XCTAssertEqual(chat.fileNames(for: "send-c1"), ["a.png"])
    }

    func testABubbleNamesItsFilesWhetherItWaitsOrGoesOut() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("see", clientId: "c1", toTray: false, files: ["1730000000-a1b2c3-log.txt"])
        XCTAssertEqual(chat.fileNames(for: "send-c1"), ["log.txt"])
        XCTAssertEqual(chat.fileNames(for: "m4"), [])
        // The agent was busy after all: the send moves to the tray with them.
        chat.posted("c1", waiting: true)
        XCTAssertEqual(chat.tray.first?.attachments, ["1730000000-a1b2c3-log.txt"])
    }

    func testTheEchoOfASendWithFilesReplacesItsBubble() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("what is this", clientId: "c1", toTray: false, files: ["1730000000-a1b2c3-a.png"])
        let echo = "@/h/.swe/data/remote/inbox/0123456789abcdef01234567/1730000000-a1b2c3-a.png what is this"
        XCTAssertTrue(ChatModel.echoes(echo, "what is this"))
        let spaced = "@/Volumes/Work Drive/.swe/data/remote/inbox/0123456789abcdef01234567/1730000000-a1b2c3-a.png what is this"
        XCTAssertTrue(ChatModel.echoes(spaced, "what is this"))
        XCTAssertFalse(ChatModel.echoes(spaced, "this"))
        XCTAssertEqual(AttachmentRules.splitEcho(echo).names, ["a.png"])
    }
}
