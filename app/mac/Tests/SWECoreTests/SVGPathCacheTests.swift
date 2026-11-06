import CoreGraphics
import Testing
@testable import SWECore

/// A glyph's path data parses once; every later layout gets the same path.
@Suite struct SVGPathCacheTests {
    @Test func aPathParsesOnceAndMatchesAFreshParse() {
        let d = "M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256Z"
        let first = SVGPath.cached(d)
        #expect(SVGPath.cached(d) === first)
        #expect(first.boundingBoxOfPath == SVGPath.path(d).boundingBoxOfPath)
    }
}
