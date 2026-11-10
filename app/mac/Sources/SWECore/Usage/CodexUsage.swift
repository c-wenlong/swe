import Foundation

/// Codex: the ChatGPT login in `<CODEX_HOME>/auth.json` and the endpoint the
/// Codex CLI's `/status` screen reads.
enum CodexUsageAPI {
    static let url = URL(string: "https://chatgpt.com/backend-api/wham/usage")!

    static func credential(home: String, files: FileReading) -> CredentialLookup {
        guard let data = files.read(home + "/auth.json") else { return .signedOut }
        guard let auth = UsageJSON.object(data) else { return .unusable("Codex auth.json could not be read") }
        let tokens = auth["tokens"] as? [String: Any] ?? [:]
        let claims = UsageJSON.string(tokens["id_token"]).flatMap(UsageJSON.jwtPayload)
        let openAI = claims?["https://api.openai.com/auth"] as? [String: Any] ?? [:]
        guard let token = UsageJSON.string(tokens["access_token"]) else {
            if UsageJSON.string(auth["OPENAI_API_KEY"]) != nil {
                return .unusable("an API-key login has no usage limits")
            }
            return .signedOut
        }
        return .found(UsageCredential(
            token: token,
            accountID: UsageJSON.string(tokens["account_id"]) ?? UsageJSON.string(openAI["chatgpt_account_id"]),
            expiresAt: nil,
            plan: PlanNames.name(.codex, UsageJSON.string(openAI["chatgpt_plan_type"])),
            email: UsageJSON.string(claims?["email"])))
    }

    static func request(_ credential: UsageCredential) -> URLRequest {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 15)
        request.httpMethod = "GET"
        request.setValue("Bearer \(credential.token)", forHTTPHeaderField: "Authorization")
        if let account = credential.accountID {
            request.setValue(account, forHTTPHeaderField: "ChatGPT-Account-Id")
        }
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("SWE", forHTTPHeaderField: "User-Agent")
        return request
    }

    static func parse(_ data: Data, credential: UsageCredential, now: Date) -> ParsedUsage? {
        guard let body = UsageJSON.object(data) else { return nil }
        let rateLimit = body["rate_limit"] as? [String: Any]
        var windows: [UsageWindow] = []
        windows += window(rateLimit?["primary_window"], slot: "5h", prefix: "", now: now)
        windows += window(rateLimit?["secondary_window"], slot: "7d", prefix: "", now: now)
        let monthly = spendLimit(body["spend_control"], now: now)
        windows += monthly
        for case let extra as [String: Any] in body["additional_rate_limits"] as? [Any] ?? [] {
            let name = displayName(UsageJSON.string(extra["limit_name"]))
            let limits = extra["rate_limit"] as? [String: Any]
            windows += window(limits?["primary_window"], slot: "5h", prefix: name, now: now)
            windows += window(limits?["secondary_window"], slot: "7d", prefix: name, now: now)
        }
        guard rateLimit != nil || !monthly.isEmpty else { return nil }
        return ParsedUsage(
            plan: PlanNames.name(.codex, UsageJSON.string(body["plan_type"])) ?? credential.plan,
            email: credential.email,
            windows: windows, monthlyCredits: credits(body["spend_control"]))
    }

    private static func credits(_ value: Any?) -> MonthlyCredits? {
        guard let control = value as? [String: Any],
              let limit = control["individual_limit"] as? [String: Any],
              let used = UsageJSON.number(limit["used"]), used >= 0,
              let cap = UsageJSON.number(limit["limit"]), cap >= 0 else { return nil }
        return MonthlyCredits(used: used, limit: cap)
    }

    /// Workspace billing can expose a monthly allowance even when wham/usage
    /// sends spend_control.individual_limit = null (notably education seats).
    static func monthlyRequest(_ credential: UsageCredential, plan: String?) -> URLRequest? {
        let workspace = ["edu", "education", "business", "enterprise", "team"]
            .contains { (plan ?? "").lowercased().hasPrefix($0) }
        guard workspace, let account = credential.accountID, !account.isEmpty else { return nil }
        var result = request(credential)
        result.url = URL(string: "https://chatgpt.com/backend-api/accounts")!
            .appendingPathComponent(account)
            .appendingPathComponent("spend-controls/current-user/monthly-usage")
        return result
    }

    struct MonthlyReading {
        var window: UsageWindow
        var credits: MonthlyCredits?
    }

    static func parseMonthly(_ data: Data) -> MonthlyReading? {
        guard let body = UsageJSON.object(data),
              let limit = body["effective_monthly_limit"] as? [String: Any],
              UsageJSON.string(limit["limit_mode"]) != "unlimited_platform_max",
              let used = UsageJSON.number(body["current_month_usage"]), used >= 0 else { return nil }
        let unit = UsageJSON.string(body["balance_unit"]) ?? "credit"
        let amount = limit["limit_amount"] as? [String: Any]
        let cap: Double?
        if let amount, UsageJSON.string(amount["unit"]) == unit {
            cap = UsageJSON.number(amount["amount"])
        } else {
            cap = unit == "credit" ? UsageJSON.number(limit["limit"]) : nil
        }
        guard let cap, cap >= 0 else { return nil }
        return MonthlyReading(
            window: UsageWindow(label: "30d", usedPercent: cap == 0 ? 100 : min(100, (used / cap) * 100), resetsAt: nil),
            credits: unit == "credit" ? MonthlyCredits(used: used, limit: cap) : nil)
    }

    /// One window, labelled by its length rather than its slot: Codex can move
    /// a sole weekly limit into the primary slot.
    private static func window(_ value: Any?, slot: String, prefix: String, now: Date) -> [UsageWindow] {
        guard let window = value as? [String: Any], let used = UsageJSON.number(window["used_percent"]) else {
            return []
        }
        let seconds = UsageJSON.number(window["limit_window_seconds"])
            ?? UsageJSON.number(window["limit_window_minutes"]).map { $0 * 60 }
        let label: String
        if let seconds, seconds > 0 {
            label = seconds >= 86_400 ? "\(Int((seconds / 86_400).rounded()))d" : "\(Int((seconds / 3600).rounded()))h"
        } else {
            label = slot
        }
        let resetsAt = UsageJSON.date(window["reset_at"]) ?? UsageJSON.date(window["resets_at"])
            ?? UsageJSON.number(window["reset_after_seconds"]).map { now.addingTimeInterval($0) }
        return [UsageWindow(
            label: prefix.isEmpty ? label : "\(prefix) \(label)",
            usedPercent: min(100, max(0, used)),
            resetsAt: resetsAt)]
    }

    /// A workspace's monthly credit cap (Edu, Business and Enterprise seats),
    /// which the Codex CLI shows as "Monthly limit". Labelled "30d" like the
    /// other harnesses' monthly windows. Used credits can pass the cap, so the
    /// percent is clamped. Some workspaces send only `reached` with no
    /// figures; a reached cap still reads as a full window.
    private static func spendLimit(_ value: Any?, now: Date) -> [UsageWindow] {
        guard let control = value as? [String: Any] else { return [] }
        guard let limit = control["individual_limit"] as? [String: Any],
              let used = UsageJSON.number(limit["used_percent"]) else {
            return control["reached"] as? Bool == true ? [UsageWindow(label: "30d", usedPercent: 100, resetsAt: nil)] : []
        }
        let resetsAt = UsageJSON.date(limit["reset_at"])
            ?? UsageJSON.number(limit["reset_after_seconds"]).map { now.addingTimeInterval($0) }
        return [UsageWindow(label: "30d", usedPercent: min(100, max(0, used)), resetsAt: resetsAt)]
    }

    /// "GPT-5.3-Codex-Spark" reads as "5.3 Spark".
    private static func displayName(_ raw: String?) -> String {
        guard var name = raw else { return "" }
        if name.hasPrefix("GPT-") { name.removeFirst(4) }
        name = name.replacingOccurrences(of: "-Codex-", with: " ", options: .caseInsensitive)
            .replacingOccurrences(of: "-Codex", with: "", options: .caseInsensitive)
            .replacingOccurrences(of: "Codex-", with: "", options: .caseInsensitive)
        return name.trimmingCharacters(in: CharacterSet(charactersIn: "- "))
    }
}
