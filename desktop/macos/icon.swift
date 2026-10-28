import Cocoa
let destination = CommandLine.arguments[1]
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
NSColor(red: 94/255, green: 230/255, blue: 173/255, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 48, y: 48, width: 928, height: 928), xRadius: 205, yRadius: 205).fill()
NSColor(red: 16/255, green: 37/255, blue: 28/255, alpha: 1).set()
let notes = NSBezierPath()
notes.lineWidth = 80
notes.lineCapStyle = .round
notes.lineJoinStyle = .round
notes.move(to: NSPoint(x: 390, y: 330))
notes.line(to: NSPoint(x: 390, y: 670))
notes.line(to: NSPoint(x: 740, y: 755))
notes.line(to: NSPoint(x: 740, y: 415))
notes.stroke()
let bar = NSBezierPath()
bar.lineWidth = 50
bar.move(to: NSPoint(x: 390, y: 555))
bar.line(to: NSPoint(x: 740, y: 640))
bar.stroke()
NSBezierPath(ovalIn: NSRect(x: 165, y: 195, width: 270, height: 210)).fill()
NSBezierPath(ovalIn: NSRect(x: 515, y: 280, width: 270, height: 210)).fill()
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: destination))
