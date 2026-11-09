import Cocoa
let destination = CommandLine.arguments[1]
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
NSColor(red: 27/255, green: 29/255, blue: 28/255, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 48, y: 48, width: 928, height: 928), xRadius: 205, yRadius: 205).fill()
NSColor(red: 103/255, green: 229/255, blue: 176/255, alpha: 1).setFill()
// Same three outlines as web/tunesaver.svg, mapped into the macOS icon inset.
let transform = AffineTransform(translationByX: 147.2, byY: 876.8)
var scaled = transform
scaled.scale(x: 5.7, y: -5.7)
let logo = NSBezierPath()
func move(_ x: CGFloat, _ y: CGFloat) { logo.move(to: NSPoint(x: x, y: y)) }
func line(_ x: CGFloat, _ y: CGFloat) { logo.line(to: NSPoint(x: x, y: y)) }
func curve(_ x1: CGFloat, _ y1: CGFloat, _ x2: CGFloat, _ y2: CGFloat, _ x: CGFloat, _ y: CGFloat) {
    logo.curve(to: NSPoint(x: x, y: y), controlPoint1: NSPoint(x: x1, y: y1), controlPoint2: NSPoint(x: x2, y: y2))
}
move(8, 35); curve(8, 19, 19, 9, 34, 9); line(94, 9)
curve(109, 9, 120, 19, 120, 35); curve(115, 26, 108, 22, 97, 22)
line(31, 22); curve(20, 22, 13, 26, 8, 35); logo.close()
move(30, 34); line(98, 34); curve(111, 34, 120, 42, 120, 54)
curve(120, 66, 111, 74, 98, 74); line(82, 74); curve(67, 74, 58, 83, 58, 98)
line(58, 112); curve(58, 120, 46, 120, 46, 112); line(46, 98)
curve(46, 76, 61, 62, 82, 62); line(98, 62); curve(104, 62, 108, 59, 108, 54)
curve(108, 49, 104, 46, 98, 46); line(30, 46); curve(20, 46, 13, 50, 10, 58)
curve(5, 45, 14, 34, 30, 34); logo.close()
move(52, 46); line(70, 46); curve(48, 57, 37, 75, 37, 98); line(37, 119)
curve(27, 119, 25, 112, 25, 103); line(25, 96); curve(25, 74, 36, 56, 52, 46); logo.close()
logo.transform(using: scaled)
logo.fill()
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: destination))
