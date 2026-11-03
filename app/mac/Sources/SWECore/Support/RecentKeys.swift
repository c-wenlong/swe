import Foundation

/// The last `capacity` distinct keys used, most recent first: an LRU
/// order for caches whose values the caller keeps.
public struct RecentKeys<Key: Hashable>: Sendable where Key: Sendable {
    public let capacity: Int
    public private(set) var keys: [Key] = []

    public init(capacity: Int) {
        self.capacity = max(1, capacity)
    }

    /// Marks `key` used. Returns the keys that fell out, oldest first,
    /// for the caller to drop.
    @discardableResult
    public mutating func touch(_ key: Key) -> [Key] {
        keys.removeAll { $0 == key }
        keys.insert(key, at: 0)
        guard keys.count > capacity else { return [] }
        let evicted = Array(keys[capacity...].reversed())
        keys.removeLast(keys.count - capacity)
        return evicted
    }

    public mutating func remove(_ key: Key) {
        keys.removeAll { $0 == key }
    }
}
