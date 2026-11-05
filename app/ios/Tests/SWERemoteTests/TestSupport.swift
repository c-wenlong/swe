import XCTest
@testable import SWERemote

// Helpers several test files share. Add one here when a second file needs it.

/// A hub event decoded from its wire JSON, the way the stream reader does.
func env(_ json: String) -> Envelope {
    try! Hub.decoder.decode(Envelope.self, from: Data(json.utf8))
}

/// A hub reply of any wire type, decoded with the hub's own decoder.
func decodeHub<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try Hub.decoder.decode(type, from: Data(json.utf8))
}

/// A paired hub at `host`, with a device id that follows the host so two
/// hubs never share an identity.
func hub(_ host: String = "mac.tail.ts.net", _ cred: String = "c", name: String? = "phone") -> PairedHub {
    PairedHub(baseURL: URL(string: "http://\(host):8477")!,
              credential: Credential(deviceId: "d-\(host)", credential: cred, name: name))
}
