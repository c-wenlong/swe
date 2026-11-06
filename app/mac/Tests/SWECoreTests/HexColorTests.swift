import Testing
@testable import SWECore

/// The one hex parser behind the app's and the widgets' theme colours.
@Suite struct HexColorTests {
    @Test func readsThreeSixAndEightDigits() throws {
        let short = try #require(HexColor.rgba("#fa0"))
        #expect(short.red == 1 && short.green == Double(0xaa) / 255 && short.blue == 0 && short.alpha == 1)
        let six = try #require(HexColor.rgba("3565c9"))
        #expect(six.red == Double(0x35) / 255 && six.blue == Double(0xc9) / 255 && six.alpha == 1)
        let eight = try #require(HexColor.rgba("#ffffff0d"))
        #expect(eight.red == 1 && eight.alpha == Double(0x0d) / 255)
        #expect(HexColor.rgba(" #000000 ")?.alpha == 1)
    }

    @Test func refusesWhatIsNotAColour() {
        #expect(HexColor.rgba("") == nil)
        #expect(HexColor.rgba("#12345") == nil)
        #expect(HexColor.rgba("#gggggg") == nil)
        #expect(HexColor.rgba("#+12345") == nil)
    }
}
