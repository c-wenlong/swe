import Foundation
import Observation

/// The non-inference tabs of Settings › Providers: Infra (deploy and
/// platform keys) and Services (search, scraping and browser APIs an
/// agent calls). A section owns its catalogue, its claim rules and the
/// store keys its marks and row order persist under, so the two tabs
/// share one implementation and never entangle each other's state.
public enum KeysSection: String, CaseIterable, Sendable {
    case infra
    case services

    public var title: String {
        switch self {
        case .infra: "Infra"
        case .services: "Services"
        }
    }

    /// The tab's known services, in the catalogue order DEFAULT_INFRA /
    /// DEFAULT_SERVICES sets in defaults.py.
    public var providers: [APIKeyProvider] {
        switch self {
        case .infra: APIKeyProviders.infra
        case .services: APIKeyProviders.services
        }
    }

    var marksKey: String { "\(rawValue)Keys" }
    var orderKey: String { "\(rawValue)Order" }

    /// Whether a keys-file variable belongs to a non-inference tab at
    /// all. Inference providers and coding-plan variables stay Inference
    /// even under a service heading (a saved Token Pass key sits under
    /// "# Vercel"), so a tab claims a row only once inference has bowed
    /// out.
    public static func claimed(forVariable name: String,
                               plans: [InferencePlan] = InferenceCatalog.all) -> Bool {
        APIKeyProviders.provider(forVariable: name) == nil
            && !plans.contains(where: { $0.keyName == name })
    }

    /// The section a variable or heading belongs to, whichever claims
    /// it. A variable names its service outright; a heading does it by
    /// folding the way `APIKeyProviders.heading` folds picks (case and
    /// spaces/underscores ignored), so "# Browser Use" finds browseruse.
    /// An inference variable or plan key claims nothing, same as
    /// `contains`.
    public static func claimed(for row: APIKeysModel.Row) -> KeysSection? {
        guard claimed(forVariable: row.name) else { return nil }
        for section in Self.allCases where section.provider(for: row) != nil {
            return section
        }
        return nil
    }

    /// Whether a keys-file row sits on this tab. A variable or heading
    /// the tab's catalogue claims is in; a name no tab claims is in only
    /// when the user moved it here (a "mark"), so a plain unknown key
    /// still lands on Inference.
    public func contains(_ row: APIKeysModel.Row, marked: Set<String>,
                         plans: [InferencePlan] = InferenceCatalog.all) -> Bool {
        guard Self.claimed(forVariable: row.name, plans: plans) else { return false }
        if provider(for: row) != nil { return true }
        return marked.contains(row.name)
    }

    /// The catalogue service a row belongs to in this section: its
    /// variable first, then its heading folded as above.
    public func provider(for row: APIKeysModel.Row) -> APIKeyProvider? {
        if let provider = provider(forVariable: row.name) { return provider }
        let heading = Self.normalize(row.provider)
        return providers.first {
            Self.normalize($0.name) == heading || $0.id == heading
        }
    }

    /// The section's catalogue service a variable names, if any.
    public func provider(forVariable name: String) -> APIKeyProvider? {
        providers.first { $0.envVars.contains(name) }
    }

    /// The section's catalogue service the picker id names, nil for
    /// "custom" or none picked.
    public func provider(forID id: String) -> APIKeyProvider? {
        providers.first { $0.id == id }
    }

    /// Rows in file order, each with its service when the catalogue
    /// knows it, then the services with no key in catalogue order — the
    /// "Any status" filter and the catalogue's Set mark cover the ones
    /// still missing.
    public func items(keys: [APIKeysModel.Row], marked: Set<String> = [],
                      providers: [APIKeyProvider]? = nil) -> [KeysItem] {
        let providers = providers ?? self.providers
        var present: [KeysItem] = []
        var seen: Set<String> = []
        for key in keys where contains(key, marked: marked) {
            let provider = provider(for: key)
            present.append(KeysItem(id: key.name, provider: provider, key: key))
            if let provider { seen.insert(provider.id) }
        }
        let missing = providers.filter { !seen.contains($0.id) }
            .map { KeysItem(id: $0.envVars[0], provider: $0, key: nil) }
        return present + missing
    }

    /// Whether a row passes the filters. The query matches the service
    /// name, the variable name and the heading the key is filed under.
    public static func matches(_ item: KeysItem, state: InferenceKeyState?, query: String,
                               stateOf: (KeysItem) -> InferenceKeyState) -> Bool {
        if let state, stateOf(item) != state { return false }
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !needle.isEmpty else { return true }
        return [item.title, item.id, item.key?.providerLabel ?? ""]
            .contains { $0.lowercased().contains(needle) }
    }

    /// What the tab's catalogue sheet lists: every service SWE knows,
    /// each one tap from its add-key flow. `hasKey` marks the ones the
    /// keys file already covers — using the same claim the table does —
    /// so a key that fills a row here reads Set there too. The rows reuse
    /// the inference sheet's Entry so all catalogues draw the same way.
    public func catalogueEntries(keys: [APIKeysModel.Row], marked: Set<String> = [])
        -> [InferenceCatalogue.Entry] {
        let filled = keys.filter { !$0.value.isEmpty && contains($0, marked: marked) }
        return providers.map { provider in
            InferenceCatalogue.Entry(target: .provider(provider),
                                     hasKey: filled.contains { self.provider(for: $0)?.id == provider.id })
        }
    }

    /// "Fly.io", "fly" and "FLY_IO" all fold to the same key — as
    /// APIKeyProviders.heading reads picks the same way, minus the dot
    /// so a heading needn't write it.
    static func normalize(_ text: String) -> String {
        text.lowercased().filter { $0.isLetter || $0.isNumber }
    }
}

/// One row of a non-inference tab: a key in the keys file whose variable
/// or heading names one of the tab's services, or a known service with
/// no key yet. The id is the variable name, so a service keeps its place
/// when its key is added.
public struct KeysItem: Identifiable, Equatable, Sendable {
    public var id: String
    /// The catalogue service the row belongs to; nil for a custom
    /// service the user moved to the tab by hand.
    public var provider: APIKeyProvider?
    /// The key as the keys file has it; nil for a service not set.
    public var key: APIKeysModel.Row?

    /// The service's own name; a custom key's heading in the file.
    public var title: String { provider?.name ?? key?.providerLabel ?? id }
    public var hasKey: Bool { !(key?.value.isEmpty ?? true) }

    public init(id: String, provider: APIKeyProvider?, key: APIKeysModel.Row?) {
        self.id = id
        self.provider = provider
        self.key = key
    }
}

/// The variables the user moved to a non-inference tab although no
/// catalogue service claims them (a custom host's token, a one-off
/// deploy key). What is kept is the set of names; a mark whose key
/// leaves the file sits harmlessly until the name returns.
@MainActor
@Observable
public final class MarkedKeys {
    private let store: KeyValueStoring
    private let key: String
    public private(set) var names: Set<String>

    public init(store: KeyValueStoring, section: KeysSection) {
        self.store = store
        key = section.marksKey
        names = Set(store.strings(forKey: key) ?? [])
    }

    public func mark(_ name: String) {
        names.insert(name)
        store.set(names.sorted(), forKey: key)
    }

    public func unmark(_ name: String) {
        names.remove(name)
        store.set(names.sorted(), forKey: key)
    }
}

/// The order you dragged a tab's rows into, under the tab's own store
/// key so it never entangles with another's. What is kept is the list of
/// ids; a row it doesn't name yet (a new key) goes at the end, and an id
/// that no longer has a row is skipped.
@MainActor
@Observable
public final class KeysOrder {
    private let store: KeyValueStoring
    private let key: String
    private var saved: [String]

    public init(store: KeyValueStoring, section: KeysSection) {
        self.store = store
        key = section.orderKey
        saved = store.strings(forKey: key) ?? []
    }

    public func sorted(_ items: [KeysItem]) -> [KeysItem] {
        let rank = Dictionary(saved.enumerated().map { ($1, $0) }, uniquingKeysWith: { first, _ in first })
        let known = items.filter { rank[$0.id] != nil }.sorted { rank[$0.id]! < rank[$1.id]! }
        return known + items.filter { rank[$0.id] == nil }
    }

    /// The row `old` becomes `new` in place, so a catalogue row whose key
    /// landed under a sibling variable keeps its dragged position.
    public func substitute(_ old: String, with new: String) {
        guard old != new, let index = saved.firstIndex(of: old), !saved.contains(new) else { return }
        saved[index] = new
        store.set(saved, forKey: key)
    }

    /// Moves `id` to sit just before `target`, or to the end for nil,
    /// among every row (filtered out or not).
    public func move(_ id: String, before target: String?, in items: [KeysItem]) {
        var order = sorted(items).map(\.id)
        guard id != target, let from = order.firstIndex(of: id) else { return }
        order.remove(at: from)
        if let target, let to = order.firstIndex(of: target) {
            order.insert(id, at: to)
        } else {
            order.append(id)
        }
        // Keep ids of rows that are gone for now (a key removed and added
        // back finds its place), after the live ones.
        saved = order + saved.filter { !order.contains($0) }
        store.set(saved, forKey: key)
    }
}
