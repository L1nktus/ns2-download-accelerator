import AppKit
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
let rect = NSRect(x: 40, y: 40, width: 944, height: 944)
let path = NSBezierPath(roundedRect: rect, xRadius: 210, yRadius: 210)
let gradient = NSGradient(starting: NSColor(srgbRed: 0.05, green: 0.68, blue: 0.64, alpha: 1), ending: NSColor(srgbRed: 0.02, green: 0.28, blue: 0.38, alpha: 1))!
gradient.draw(in: path, angle: -55)
let symbol = NSImage(systemSymbolName: "arrow.up.right", accessibilityDescription: nil)!.withSymbolConfiguration(.init(pointSize: 490, weight: .bold))!
let colored = NSImage(size: symbol.size)
colored.lockFocus(); NSColor.white.set(); NSRect(origin: .zero, size: symbol.size).fill(); symbol.draw(at: .zero, from: .zero, operation: .destinationIn, fraction: 1); colored.unlockFocus()
colored.draw(in: NSRect(x: 277, y: 277, width: 470, height: 470))
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
