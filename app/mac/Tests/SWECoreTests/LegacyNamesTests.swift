import Foundation
import Testing
@testable import SWECore

/// The pre-rename names the migrations read. A blanket quiver -> swe
/// rewrite once turned each into the new name, which made the migration
/// read from itself and never carry anything over.
@Suite struct LegacyNamesTests {
    @Test func theLegacyNamesAreQuiverAppsOwn() {
        #expect(LegacyDefaults.legacyDomain == "com.cwenlong.quiver")
        #expect(ServiceCredentials.legacyKeychainService == "com.cwenlong.quiver.services")
        #expect(FanHelper.legacyLabel == "com.cwenlong.quiver.fan")
    }

    @Test func eachLegacyNameDiffersFromItsSuccessor() {
        #expect(ServiceCredentials.legacyKeychainService != ServiceCredentials.keychainService)
        #expect(LegacyDefaults.legacyDomain != "com.cwenlong.swe")
    }

    @Test func defaultsCopyFromTheLegacyDomain() throws {
        let legacy = "test.legacy.\(UUID().uuidString)"
        let target = "test.target.\(UUID().uuidString)"
        let old = try #require(UserDefaults(suiteName: legacy))
        let new = try #require(UserDefaults(suiteName: target))
        defer {
            old.removePersistentDomain(forName: legacy)
            new.removePersistentDomain(forName: target)
        }
        old.set("/opt/swe", forKey: "swePath")
        old.set("old", forKey: "kept")
        new.set("new", forKey: "kept")
        LegacyDefaults.migrate(into: new, from: legacy)
        #expect(new.string(forKey: "swePath") == "/opt/swe")
        #expect(new.string(forKey: "kept") == "new")
        #expect(new.bool(forKey: LegacyDefaults.flagKey))
    }
}
