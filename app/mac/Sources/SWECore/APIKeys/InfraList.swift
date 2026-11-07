import Foundation
import Observation

/// One row of Settings › Providers › Infra: a key in the keys file whose
/// variable or heading names an infra service, or a known service with
/// no key yet. The id is the variable name, so a service keeps its
/// place when its key is added.
public struct InfraItem: Identifiable, Equatable, Sendable {
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

public enum InfraList {
    /// Whether a keys-file row is infra. An inference provider's
    /// variable stays inference even under an infra heading; an infra
    /// variable or a heading naming an infra service is infra; a name no
    /// provider claims is infra only when the user moved it here (a
    /// "mark"), so a plain unknown key still lands on Inference.
    public static func isInfra(_ row: APIKeysModel.Row, marked: Set<String>) -> Bool {
        if APIKeyProviders.provider(forVariable: row.name) != nil { return false }
        if infraProvider(for: row) != nil { return true }
        return marked.contains(row.name)
    }

    /// The catalogue service a row belongs to: its variable first, then
    /// its heading folded the way `heading(for:in:)` folds picks (case
    /// and spaces/underscores ignored).
    public static func infraProvider(for row: APIKeysModel.Row) -> APIKeyProvider? {
        if let provider = APIKeyProviders.infraProvider(forVariable: row.name) { return provider }
        let heading = normalize(row.provider)
        return APIKeyProviders.infra.first {
            normalize($0.name) == heading || $0.id == heading
        }
    }

    /// Infra rows in file order, each with its service when the
    /// catalogue knows it, then the services with no key in catalogue
    /// order — the "Any status" filter and the catalogue's Set mark
    /// cover the ones still missing.
    public static func items(keys: [APIKeysModel.Row], marked: Set<String> = [],
                             providers: [APIKeyProvider] = APIKeyProviders.infra) -> [InfraItem] {
        var present: [InfraItem] = []
        var seen: Set<String> = []
        for key in keys where isInfra(key, marked: marked) {
            let provider = infraProvider(for: key)
            present.append(InfraItem(id: key.name, provider: provider, key: key))
            if let provider { seen.insert(provider.id) }
        }
        let missing = providers.filter { !seen.contains($0.id) }
            .map { InfraItem(id: $0.envVars[0], provider: $0, key: nil) }
        return present + missing
    }

    /// Whether a row passes the filters. The query matches the service
    /// name, the variable name and the heading the key is filed under.
    public static func matches(_ item: InfraItem, state: InferenceKeyState?, query: String,
                               stateOf: (InfraItem) -> InferenceKeyState) -> Bool {
        if let state, stateOf(item) != state { return false }
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !needle.isEmpty else { return true }
        return [item.title, item.id, item.key?.providerLabel ?? ""]
            .contains { $0.lowercased().contains(needle) }
    }

    /// "Fly.io", "fly" and "FLY_IO" all fold to the same key — as
    /// APIKeyProviders.heading reads picks the same way, minus the dot
    /// so a heading needn't write it.
    private static func normalize(_ text: String) -> String {
        text.lowercased().filter { $0.isLetter || $0.isNumber }
    }
}

/// The variables the user moved to the Infra tab although no catalogue
/// service claims them (a custom host's token, a one-off deploy key).
/// What is kept is the set of names; a mark whose key leaves the file
/// sits harmlessly until the name returns.
@MainActor
@Observable
public final class InfraMarks {
    static let key = "infraKeys"

    private let store: KeyValueStoring
    public private(set) var names: Set<String>

    public init(store: KeyValueStoring) {
        self.store = store
        names = Set(store.strings(forKey: Self.key) ?? [])
    }

    public func mark(_ name: String) {
        names.insert(name)
        store.set(names.sorted(), forKey: Self.key)
    }

    public func unmark(_ name: String) {
        names.remove(name)
        store.set(names.sorted(), forKey: Self.key)
    }
}

/// The order you dragged the Infra rows into, under its own store key so
/// it never entangles with Inference's. What is kept is the list of ids;
/// a row it doesn't name yet (a new key) goes at the end, and an id that
/// no longer has a row is skipped.
@MainActor
@Observable
public final class InfraOrder {
    static let key = "infraOrder"

    private let store: KeyValueStoring
    private var saved: [String]

    public init(store: KeyValueStoring) {
        self.store = store
        saved = store.strings(forKey: Self.key) ?? []
    }

    public func sorted(_ items: [InfraItem]) -> [InfraItem] {
        let rank = Dictionary(saved.enumerated().map { ($1, $0) }, uniquingKeysWith: { first, _ in first })
        let known = items.filter { rank[$0.id] != nil }.sorted { rank[$0.id]! < rank[$1.id]! }
        return known + items.filter { rank[$0.id] == nil }
    }

    /// Moves `id` to sit just before `target`, or to the end for nil,
    /// among every row (filtered out or not).
    public func move(_ id: String, before target: String?, in items: [InfraItem]) {
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
        store.set(saved, forKey: Self.key)
    }
}

/// What the Infra tab's catalogue sheet lists: every infra service SWE
/// knows, each one tap from its add-key flow. `hasKey` marks the ones
/// the keys file already covers, so the sheet can say so instead of
/// offering a duplicate. The rows reuse the inference sheet's Entry so
/// both catalogues draw the same way.
public enum InfraCatalogue {
    /// Every infra service, in DEFAULT_INFRA's order.
    public static func entries(keys: [APIKeysModel.Row],
                               providers: [APIKeyProvider] = APIKeyProviders.infra)
        -> [InferenceCatalogue.Entry] {
        let filled = Set(keys.filter { !$0.value.isEmpty }.map(\.name))
        return providers.map {
            InferenceCatalogue.Entry(target: .provider($0),
                                     hasKey: $0.envVars.contains { filled.contains($0) })
        }
    }
}
