import Foundation
import Testing
@testable import SWECore

@Suite struct CodexUsageTests {
    @Test func aMissingAuthFileIsSignedOutAndSendsNothing() async {
        let h = UsageHarness()
        let usage = await h.makeService().usage(for: codexKey, force: true)
        #expect(usage.status == .signedOut)
        #expect(usage.windows.isEmpty)
        #expect(usage.fetchedAt == nil)
        #expect(usage.isStale == false)
        #expect(h.transport.requests.isEmpty)
    }

    @Test func readsBothWindowsWithTheTokenAndAccountHeader() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(200, body: try fixture("codex-usage"))

        let usage = await h.makeService().usage(for: codexKey, force: false)

        let request = try #require(h.transport.requests.first)
        #expect(request.url?.absoluteString == "https://chatgpt.com/backend-api/wham/usage")
        #expect(request.httpMethod == "GET")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer codex-access-SECRET-1")
        #expect(request.value(forHTTPHeaderField: "ChatGPT-Account-Id") == "acct-123")
        #expect(usage == AccountUsage(
            status: .ok, plan: "Pro 20x", email: "dev@example.com",
            windows: [
                UsageWindow(label: "5h", usedPercent: 23, resetsAt: Date(timeIntervalSince1970: 1_791_727_200)),
                UsageWindow(label: "7d", usedPercent: 41.5, resetsAt: Date(timeIntervalSince1970: 1_792_051_200)),
            ],
            fetchedAt: usageEpoch, isStale: false))
    }

    @Test func aSoleWeeklyWindowInThePrimarySlotIsLabelledByItsLength() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(200, body: try fixture("codex-usage-weekly-only"))

        let usage = await h.makeService().usage(for: codexKey, force: false)

        #expect(usage.plan == "Plus")
        #expect(usage.windows == [
            // No reset_at, so reset_after_seconds counts from the fetch.
            UsageWindow(label: "7d", usedPercent: 100, resetsAt: usageEpoch.addingTimeInterval(43_200)),
        ])
    }

    @Test func anEduWorkspacesMonthlyCreditCapIsA30dWindow() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        // The shape the Codex CLI parses: used credits can pass the cap.
        let body = #"""
        {"plan_type": "edu",
         "rate_limit": {"primary_window": {"used_percent": 0, "limit_window_seconds": 18000, "reset_at": 1791727200},
                        "secondary_window": {"used_percent": 86, "limit_window_seconds": 604800, "reset_at": 1792051200}},
         "spend_control": {"reached": true, "individual_limit": {
            "source": "workspace_spend_controls", "limit": "2500", "used": "2504", "remaining": "0",
            "used_percent": 101, "remaining_percent": 0, "reset_after_seconds": 86400, "reset_at": 1792300000}}}
        """#
        h.transport.reply(200, body: Data(body.utf8))

        let usage = await h.makeService().usage(for: codexKey, force: false)

        #expect(usage.monthlyCredits == MonthlyCredits(used: 2504, limit: 2500))
        #expect(usage.plan == "Edu")
        #expect(usage.windows == [
            UsageWindow(label: "5h", usedPercent: 0, resetsAt: Date(timeIntervalSince1970: 1_791_727_200)),
            UsageWindow(label: "7d", usedPercent: 86, resetsAt: Date(timeIntervalSince1970: 1_792_051_200)),
            UsageWindow(label: "30d", usedPercent: 100, resetsAt: Date(timeIntervalSince1970: 1_792_300_000)),
        ])
    }

    @Test func monthlyCreditsSurviveCachedAndFailedReads() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(200, body: Data(#"{"spend_control":{"individual_limit":{"used_percent":0,"used":"0","limit":"700"}}}"#.utf8))
        let service = h.makeService()
        let fresh = await service.usage(for: codexKey, force: true)
        #expect(fresh.monthlyCredits == MonthlyCredits(used: 0, limit: 700))
        let cached = await service.cachedUsage(for: codexKey)
        #expect(cached?.monthlyCredits == fresh.monthlyCredits)
        h.transport.reply(500, body: Data())
        let stale = await service.usage(for: codexKey, force: true)
        #expect(stale.isStale)
        #expect(stale.monthlyCredits == fresh.monthlyCredits)
        let decoded = try JSONDecoder().decode(AccountUsage.self, from: JSONEncoder().encode(fresh))
        #expect(decoded.monthlyCredits == fresh.monthlyCredits)
    }

    @Test(arguments: ["null", "{}", #"{"used":true,"limit":700}"#,
                      #"{"used":0,"limit":-1}"#, #"{"used":"NaN","limit":700}"#])
    func missingOrInvalidCreditFiguresStayHidden(limit: String) throws {
        let body = "{\"rate_limit\":{},\"spend_control\":{\"individual_limit\":\(limit)}}"
        let parsed = try #require(CodexUsageAPI.parse(Data(body.utf8), credential: UsageCredential(
            token: "t", accountID: nil, expiresAt: nil, plan: nil, email: nil), now: usageEpoch))
        #expect(parsed.monthlyCredits == nil)
    }

    @Test func educationMonthlyAllowanceComesFromWorkspaceBilling() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(200, body: Data(#"{"plan_type":"education","rate_limit":{"primary_window":{"used_percent":53}},"spend_control":{"reached":false,"individual_limit":null}}"#.utf8))
        h.transport.reply(200, body: Data(#"{"balance_unit":"credit","effective_monthly_limit":{"limit":700,"enforcement_mode":"HARD_CAP","limit_mode":"amount_credits"},"current_month_usage":502.99}"#.utf8))
        let service = h.makeService()
        let usage = await service.usage(for: codexKey, force: true)
        #expect(usage.windows.map(\.label) == ["5h", "30d"])
        #expect(abs((usage.windows.last?.usedPercent ?? 0) - 71.8557) < 0.001)
        #expect(usage.monthlyCredits == MonthlyCredits(used: 502.99, limit: 700))
        let request = try #require(h.transport.requests.last)
        #expect(request.url?.path == "/backend-api/accounts/acct-123/spend-controls/current-user/monthly-usage")
        #expect(request.value(forHTTPHeaderField: "ChatGPT-Account-Id") == "acct-123")
        let cached = await service.cachedUsage(for: codexKey)
        #expect(cached?.windows == usage.windows)
        #expect(cached?.monthlyCredits == usage.monthlyCredits)
    }

    @Test func typedDollarLimitsDoNotBecomeCredits() throws {
        let body = #"{"balance_unit":"usd","effective_monthly_limit":{"limit_amount":{"unit":"usd","amount":"28"}},"current_month_usage":7}"#
        let monthly = try #require(CodexUsageAPI.parseMonthly(Data(body.utf8)))
        #expect(monthly.window.usedPercent == 25)
        #expect(monthly.credits == nil)
    }

    @Test(arguments: [(0.0, 0.0), (700.0, 701.0)])
    func zeroAndExceededLimitsAreClamped(cap: Double, used: Double) throws {
        let body = "{\"effective_monthly_limit\":{\"limit\":\(cap)},\"current_month_usage\":\(used)}"
        let monthly = try #require(CodexUsageAPI.parseMonthly(Data(body.utf8)))
        #expect(monthly.window.usedPercent == 100)
        #expect(monthly.credits == MonthlyCredits(used: used, limit: cap))
    }

    @Test func concurrentFailureCannotEraseBillingCooldownWithoutAReading() throws {
        let h = UsageHarness()
        let store = UsageCacheStore(url: h.cacheURL, now: { usageEpoch })
        let until = usageEpoch.addingTimeInterval(3600)
        store.save(StoredAccount(fingerprint: "login", reading: nil, cooldownUntil: nil,
                                 lastFailure: nil, monthlyRetryAt: until), for: codexKey)
        // A concurrent request started before the first response and failed.
        store.save(StoredAccount(fingerprint: "login", reading: nil, cooldownUntil: nil,
                                 lastFailure: StoredFailure(at: usageEpoch, status: .error("offline"))), for: codexKey)
        let kept = try #require(store.entry(for: codexKey))
        #expect(kept.reading == nil)
        #expect(kept.monthlyRetryAt == until)
        // A successful refresh cannot shorten a later wait saved elsewhere.
        store.save(StoredAccount(fingerprint: "login", reading: nil, cooldownUntil: nil,
                                 lastFailure: nil, monthlyRetryAt: until.addingTimeInterval(-600)), for: codexKey)
        #expect(store.entry(for: codexKey)?.monthlyRetryAt == until)
        // A genuinely new login starts clean.
        store.save(StoredAccount(fingerprint: "new-login", reading: nil, cooldownUntil: nil,
                                 lastFailure: nil), for: codexKey)
        #expect(store.entry(for: codexKey)?.monthlyRetryAt == nil)
    }

    @Test func billingRetryAfterSurvivesRestartAndForcedRefresh() async {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        let standard = Data(#"{"plan_type":"education","rate_limit":{"primary_window":{"used_percent":53}}}"#.utf8)
        h.transport.reply(200, body: standard)
        h.transport.reply(429, headers: ["Retry-After": "3600"])
        let first = await h.makeService().usage(for: codexKey, force: true)
        #expect(first.status == .ok)
        #expect(first.windows.map(\.label) == ["5h"])
        h.clock.advance(600)
        h.transport.reply(200, body: standard)
        let refreshed = await h.makeService().usage(for: codexKey, force: true)
        #expect(refreshed.status == .ok)
        #expect(h.transport.requests.count == 3)
        h.clock.advance(3001)
        h.transport.reply(200, body: standard)
        h.transport.reply(200, body: Data(#"{"effective_monthly_limit":{"limit":700},"current_month_usage":350}"#.utf8))
        let resumed = await h.makeService().usage(for: codexKey, force: true)
        #expect(h.transport.requests.count == 5)
        #expect(resumed.windows.last?.usedPercent == 50)
        #expect(resumed.monthlyCredits == MonthlyCredits(used: 350, limit: 700))
    }

    @Test func unavailableMonthlyBillingKeepsTheOtherWindows() async {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(200, body: Data(#"{"plan_type":"education","rate_limit":{"primary_window":{"used_percent":53}}}"#.utf8))
        h.transport.reply(403)
        let usage = await h.makeService().usage(for: codexKey, force: true)
        #expect(usage.status == .ok)
        #expect(usage.windows.map(\.label) == ["5h"])
    }

    @Test(arguments: [
        #"{"effective_monthly_limit":{"limit":700,"limit_mode":"unlimited_platform_max"},"current_month_usage":5}"#,
        #"{"effective_monthly_limit":{"limit":-1},"current_month_usage":5}"#,
        #"{"balance_unit":"usd","effective_monthly_limit":{"limit":700},"current_month_usage":5}"#,
        #"{"effective_monthly_limit":{"limit":700},"current_month_usage":true}"#,
    ])
    func invalidMonthlyBillingIsHidden(body: String) {
        #expect(CodexUsageAPI.parseMonthly(Data(body.utf8)) == nil)
    }

    @Test(arguments: [
        ("education", "Education"), ("edu", "Edu"), ("edu_plus", "Edu Plus"), ("edu_pro", "Edu Pro"),
        ("pro", "Pro 20x"), ("self_serve_business_prolite", "Business 5x"),
    ])
    func educationPlansKeepTheirOwnNames(slug: String, name: String) {
        #expect(PlanNames.name(.codex, slug) == name)
    }

    @Test func aSpendCapAloneStillCounts() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        let body = #"{"spend_control": {"reached": false, "individual_limit": {"used_percent": 32, "reset_after_seconds": 43200}}}"#
        h.transport.reply(200, body: Data(body.utf8))

        let usage = await h.makeService().usage(for: codexKey, force: false)

        #expect(usage.status == .ok)
        #expect(usage.windows == [UsageWindow(label: "30d", usedPercent: 32, resetsAt: usageEpoch.addingTimeInterval(43_200))])
    }

    @Test func aReachedCapWithNoFiguresIsAFullWindow() throws {
        // What an education seat at its cap sends: reached, but no individual_limit.
        let body = #"{"plan_type": "education", "rate_limit": {"primary_window": {"used_percent": 0, "limit_window_seconds": 18000}}, "spend_control": {"reached": true, "individual_limit": null}}"#
        let parsed = try #require(CodexUsageAPI.parse(Data(body.utf8), credential: UsageCredential(
            token: "t", accountID: nil, expiresAt: nil, plan: nil, email: nil), now: usageEpoch))
        #expect(parsed.plan == "Education")
        #expect(parsed.windows.last == UsageWindow(label: "30d", usedPercent: 100, resetsAt: nil))
    }

    @Test func noSpendControlAddsNoWindow() throws {
        let body = #"{"rate_limit": {"primary_window": {"used_percent": 5, "limit_window_seconds": 18000}}, "spend_control": {"reached": false, "individual_limit": null}}"#
        let parsed = try #require(CodexUsageAPI.parse(Data(body.utf8), credential: UsageCredential(
            token: "t", accountID: nil, expiresAt: nil, plan: nil, email: nil), now: usageEpoch))
        #expect(parsed.windows.map(\.label) == ["5h"])
    }

    @Test func identityComesFromTheIDTokenWhenTheResponseLacksIt() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON(accountID: nil, email: "other@example.com", plan: "prolite"))
        let body = #"""
        {"rate_limit": {"primary_window": {"used_percent": 5, "limit_window_seconds": 18000, "reset_at": 1791727200}},
         "additional_rate_limits": [{"limit_name": "GPT-5.3-Codex-Spark", "rate_limit": {
            "primary_window": {"used_percent": 9, "limit_window_seconds": 18000, "reset_at": 1791727200},
            "secondary_window": null}}]}
        """#
        h.transport.reply(200, body: Data(body.utf8))

        let usage = await h.makeService().usage(for: codexKey, force: false)

        let request = try #require(h.transport.requests.first)
        #expect(request.value(forHTTPHeaderField: "ChatGPT-Account-Id") == "acct-from-jwt")
        #expect(usage.email == "other@example.com")
        #expect(usage.plan == "Pro 5x")
        #expect(usage.windows.map(\.label) == ["5h", "5.3 Spark 5h"])
        #expect(usage.windows.map(\.usedPercent) == [5, 9])
    }

    @Test func anAPIKeyLoginHasNoLimitsToRead() async {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", #"{"auth_mode": "apikey", "OPENAI_API_KEY": "sk-SECRET"}"#)
        let usage = await h.makeService().usage(for: codexKey, force: false)
        #expect(usage.status == .error("an API-key login has no usage limits"))
        #expect(h.transport.requests.isEmpty)
    }
}
