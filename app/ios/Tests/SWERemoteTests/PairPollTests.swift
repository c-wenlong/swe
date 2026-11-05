import XCTest
@testable import SWERemote

final class PairPollTests: XCTestCase {
    func testThePollSecretRidesAHeaderNotTheURL() throws {
        let client = HubClient(baseURL: URL(string: "http://hub:8477")!, credential: nil)
        let req = client.pairPollRequest(id: "r 1/x", secret: "s3cr3t+/=")
        let url = try XCTUnwrap(req.url)
        XCTAssertEqual(req.httpMethod, "GET")
        XCTAssertEqual(url.absoluteString, "http://hub:8477/v1/pair/request/r%201%2Fx")
        XCTAssertNil(url.query)
        XCTAssertFalse(url.absoluteString.contains("s3cr3t"))
        XCTAssertEqual(req.value(forHTTPHeaderField: "X-Pair-Secret"), "s3cr3t+/=")
        XCTAssertNil(req.value(forHTTPHeaderField: "Authorization"))
    }
}
