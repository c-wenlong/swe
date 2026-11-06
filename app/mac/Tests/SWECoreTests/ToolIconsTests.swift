import AppKit
import Testing
@testable import SWECore

/// The bundled marks Settings draws for editors and terminals that are not
/// installed. Only the package's own resources are read.
@Suite struct ToolIconsTests {
    @Test func everyOfferedEditorAndTerminalHasABundledMark() {
        let ids = CodeEditors.candidates.map(\.icon) + TerminalApp.allCases.compactMap(\.toolIconID)
        for id in ids {
            #expect(ToolIcons.image(for: id) != nil, "\(id)")
        }
    }

    @Test func svgMarksAreTemplatesAndRastersAreNot() {
        // SVGs dim with a disabled tab's title; the PNG keeps its colours.
        #expect(ToolIcons.image(for: "zed")?.isTemplate == true)
        // Devin's editor entry draws its harness logo.
        #expect(ToolIcons.image(for: "devin")?.isTemplate == true)
        #expect(ToolIcons.image(for: "gram")?.isTemplate == false)
        #expect(ToolIcons.image(for: "no-such-tool") == nil)
    }
}
