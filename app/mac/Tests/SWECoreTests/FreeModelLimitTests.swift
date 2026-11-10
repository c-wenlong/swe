import Foundation
import Testing
@testable import SWECore

@Suite struct FreeModelLimitTests {
    @Test func decodesBlockWithoutChangingPaidQuotaAndExpires() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .secondsSince1970
        let data = Data(#"{"status":"ok","used_percent":4,"limit_reached":false,"free_model_reset_at":1800000000}"#.utf8)
        let limit = try decoder.decode(RateLimit.self, from: data)
        #expect(limit.usedPercent == 4)
        #expect(!limit.limitReached)
        #expect(limit.freeModelLimitReached(now: Date(timeIntervalSince1970: 1799999900)))
        #expect(!limit.freeModelLimitReached(now: Date(timeIntervalSince1970: 1800000000)))
        let cached = try JSONDecoder().decode(RateLimit.self, from: JSONEncoder().encode(limit))
        #expect(cached.freeModelResetAt == limit.freeModelResetAt)
    }

    @Test func olderAndMalformedPayloadsStayUnknown() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        for json in [#"{"status":"ok","used_percent":4}"#,
                     #"{"status":"ok","used_percent":4,"free_model_reset_at":"bad"}"#] {
            let limit = try decoder.decode(RateLimit.self, from: Data(json.utf8))
            #expect(limit.freeModelResetAt == nil)
            #expect(!limit.freeModelLimitReached(now: .now))
        }
    }
}
