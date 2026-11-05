/// `#rrggbb` or CSS-style `#rgb`, as the profile store accepts them.
public enum HexColor {
    public struct RGB: Equatable, Sendable { public var red, green, blue: UInt8 }

    public static func rgb(_ hex: String) -> RGB? {
        var digits = Substring(hex)
        if digits.hasPrefix("#") { digits = digits.dropFirst() }
        if digits.count == 3 { digits = Substring(digits.map { "\($0)\($0)" }.joined()) }
        guard digits.count == 6, let value = UInt32(digits, radix: 16) else { return nil }
        return RGB(red: UInt8((value >> 16) & 0xff), green: UInt8((value >> 8) & 0xff), blue: UInt8(value & 0xff))
    }

    /// `#rgb`, `#rrggbb` or `#rrggbbaa` (the `#` optional) as 0...1
    /// channels; nil for anything else. The one parser behind the app's
    /// and the widgets' appearance-following colours.
    public static func rgba(_ hex: String) -> (red: Double, green: Double, blue: Double, alpha: Double)? {
        var digits = Substring(hex.trimmingCharacters(in: .whitespaces))
        if digits.hasPrefix("#") { digits = digits.dropFirst() }
        if digits.count == 3 { digits = Substring(digits.map { "\($0)\($0)" }.joined()) }
        guard digits.count == 6 || digits.count == 8,
              digits.allSatisfy(\.isHexDigit), let value = UInt64(digits, radix: 16) else { return nil }
        let alpha = digits.count == 8
        func channel(_ shift: UInt64) -> Double { Double((value >> shift) & 0xff) / 255 }
        return (channel(alpha ? 24 : 16), channel(alpha ? 16 : 8), channel(alpha ? 8 : 0),
                alpha ? channel(0) : 1)
    }
}
