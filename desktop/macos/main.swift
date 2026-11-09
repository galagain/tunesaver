import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, WKDownloadDelegate {
    var window: NSWindow!
    var browser: WKWebView!
    var backend: Process?
    var parentPipe: Pipe?
    var output = Data()
    var startupTimer: Timer?
    var origin: URL?
    var qobuzWindow: NSWindow?
    var qobuzBrowser: WKWebView?
    var shuttingDown = false
    #if SMOKE_TEST
    let dataRoot = URL(fileURLWithPath: ProcessInfo.processInfo.environment["TUNESAVER_SMOKE_DATA"]!)
    #else
    let dataRoot = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("TuneSaver")
    #endif

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let id = Bundle.main.bundleIdentifier,
           let existing = NSRunningApplication.runningApplications(withBundleIdentifier: id).first(where: { $0.processIdentifier != getpid() }) {
            existing.activate(options: [.activateAllWindows])
            NSApp.terminate(nil)
            return
        }
        makeMenu()
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.mediaTypesRequiringUserActionForPlayback = []
        config.userContentController.add(self, name: "desktop")
        browser = WKWebView(frame: .zero, configuration: config)
        browser.navigationDelegate = self
        browser.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 840), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "TuneSaver"
        window.minSize = NSSize(width: 920, height: 640)
        window.contentView = browser
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        browser.loadHTMLString("<body style='background:#151a17;color:#d5eadb;font:18px -apple-system;padding:60px'><h1>TuneSaver</h1><p>Ouverture de ta bibliothèque…</p></body>", baseURL: nil)
        startBackend()
    }

    func makeMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "TuneSaver")
        appMenu.addItem(withTitle: "À propos de TuneSaver", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quitter TuneSaver", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        menu.addItem(appItem)
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Édition")
        for (title, action, key) in [("Annuler", "undo:", "z"), ("Couper", "cut:", "x"), ("Copier", "copy:", "c"), ("Coller", "paste:", "v"), ("Tout sélectionner", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        editItem.submenu = edit
        menu.addItem(editItem)
        let windowItem = NSMenuItem()
        let windows = NSMenu(title: "Fenêtre")
        windows.addItem(withTitle: "Réduire", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowItem.submenu = windows
        menu.addItem(windowItem)
        NSApp.windowsMenu = windows
        NSApp.mainMenu = menu
    }

    func startBackend() {
        guard let resources = Bundle.main.resourceURL else { fail("Les fichiers de l’application sont introuvables."); return }
        do {
            try FileManager.default.createDirectory(at: dataRoot.appendingPathComponent("logs"), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let process = Process()
            process.executableURL = resources.appendingPathComponent("python/bin/python3.13")
            process.arguments = ["-s", resources.appendingPathComponent("src/desktop.py").path, "--resources", resources.path, "--data-dir", dataRoot.path, "--parent-watch"]
            var environment = ProcessInfo.processInfo.environment
            // Never inherit a developer's Python installation or dynamic library paths.
            for key in ["PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES"] { environment.removeValue(forKey: key) }
            environment["PYTHONUNBUFFERED"] = "1"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONNOUSERSITE"] = "1"
            process.environment = environment
            let stdout = Pipe()
            let stdin = Pipe()
            parentPipe = stdin
            process.standardInput = stdin
            process.standardOutput = stdout
            let logURL = dataRoot.appendingPathComponent("logs/desktop.log")
            FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
            process.standardError = try FileHandle(forWritingTo: logURL)
            stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let bytes = handle.availableData
                guard !bytes.isEmpty else { handle.readabilityHandler = nil; return }
                DispatchQueue.main.async { self?.consume(bytes) }
            }
            process.terminationHandler = { [weak self] _ in
                DispatchQueue.main.async {
                    guard let self, !self.shuttingDown else { return }
                    self.fail("Le moteur local s’est arrêté. Relance TuneSaver. Le diagnostic est disponible dans Bibliothèque/Application Support/TuneSaver/logs/desktop.log.")
                }
            }
            backend = process
            try process.run()
            startupTimer = Timer.scheduledTimer(withTimeInterval: 90, repeats: false) { [weak self] _ in
                self?.fail("Le démarrage prend trop de temps. Vérifie que le Trousseau macOS est déverrouillé, puis relance TuneSaver.")
            }
        } catch { fail("Impossible de démarrer le moteur local : \(error.localizedDescription)") }
    }

    func consume(_ bytes: Data) {
        output.append(bytes)
        while let range = output.range(of: Data([10])) {
            let line = String(data: output[..<range.lowerBound], encoding: .utf8) ?? ""
            output.removeSubrange(..<range.upperBound)
            if line.hasPrefix("TUNESAVER_READY "), let url = URL(string: String(line.dropFirst(16))), url.host == "127.0.0.1", url.scheme == "http" {
                startupTimer?.invalidate()
                origin = URL(string: "http://127.0.0.1:\(url.port!)")
                browser.load(URLRequest(url: url))
            }
        }
        if output.count > 65536 { output.removeAll() }
    }

    func isLocal(_ url: URL?) -> Bool {
        guard let url, let origin else { return false }
        return url.scheme == origin.scheme && url.host == origin.host && url.port == origin.port
    }

    #if SMOKE_TEST
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard isLocal(webView.url), webView === browser else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            self.browser.evaluateJavaScript("JSON.stringify({local: publicConfig.desktop, library: !document.querySelector('#app-view').hidden, accounts: document.querySelector('#desktop-accounts')?.open, errors: document.querySelector('#auth-message').textContent})") { value, error in
                guard error == nil, let result = value as? String, result.contains("\"local\":true"), result.contains("\"library\":true"), result.contains("\"accounts\":true") else {
                    fputs("NATIVE_SMOKE_FAILED \(String(describing: value)) \(String(describing: error))\n", stderr)
                    NSApp.terminate(nil)
                    return
                }
                print("NATIVE_SMOKE_OK \(result)")
                self.browser.takeSnapshot(with: nil) { image, _ in
                    if let data = image?.tiffRepresentation, let bitmap = NSBitmapImageRep(data: data), let png = bitmap.representation(using: .png, properties: [:]) {
                        try? png.write(to: self.dataRoot.appendingPathComponent("native-window.png"))
                    }
                    NSApp.terminate(nil)
                }
            }
        }
    }
    #endif

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if webView === qobuzBrowser {
            decisionHandler(url.scheme == "https" || url.absoluteString == "about:blank" ? .allow : .cancel)
        } else if isLocal(url) || url.absoluteString == "about:blank" || url.scheme == "blob" {
            decisionHandler(action.shouldPerformDownload ? .download : .allow)
        } else {
            if ["https", "http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let attachment = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")?.lowercased().hasPrefix("attachment") == true
        decisionHandler(!response.canShowMIMEType || attachment ? .download : .allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let url = action.request.url else { return nil }
        if isLocal(url) { browser.load(action.request) }
        else if url.scheme == "https" { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.url : nil) }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let alert = NSAlert()
        alert.messageText = "L’export n’a pas abouti"
        alert.informativeText = error.localizedDescription
        alert.beginSheetModal(for: window)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame else { return }
        if message.name == "qobuzSession", message.webView === qobuzBrowser,
           message.frameInfo.securityOrigin.host == "play.qobuz.com", message.frameInfo.securityOrigin.protocol == "https",
           let data = message.body as? [String: String], let id = data["user_id"], let token = data["token"],
           !id.isEmpty, !token.isEmpty, token.count <= 8192 {
            guard let json = try? JSONSerialization.data(withJSONObject: data), let text = String(data: json, encoding: .utf8) else { return }
            browser.evaluateJavaScript("window.tunesaverConnectQobuz(\(text))", completionHandler: nil)
            qobuzBrowser?.configuration.userContentController.removeScriptMessageHandler(forName: "qobuzSession")
            qobuzWindow?.close()
            qobuzWindow = nil
            qobuzBrowser = nil
            return
        }
        guard message.name == "desktop", message.webView === browser, isLocal(message.frameInfo.request.url),
              let body = message.body as? [String: String] else { return }
        if body["action"] == "openMusic" { NSWorkspace.shared.open(dataRoot.appendingPathComponent("Music")) }
        if body["action"] == "qobuzLogin" { openQobuz() }
    }

    func openQobuz() {
        if let existing = qobuzWindow, existing.isVisible { existing.makeKeyAndOrderFront(nil); return }
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.userContentController.add(self, name: "qobuzSession")
        let script = """
        if (location.origin === 'https://play.qobuz.com') {
          let sent = false;
          setInterval(() => {
            if (sent) return;
            try {
              const user = JSON.parse(localStorage.getItem('localuser') || 'null');
              if (user?.id && user?.user_auth_token) {
                sent = true;
                window.webkit.messageHandlers.qobuzSession.postMessage({user_id:String(user.id), token:String(user.user_auth_token)});
              }
            } catch (_) {}
          }, 1000);
        }
        """
        config.userContentController.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = self
        view.uiDelegate = self
        let popup = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 740), styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        popup.title = "Connexion Qobuz — play.qobuz.com"
        popup.isReleasedWhenClosed = false
        popup.contentView = view
        popup.center()
        qobuzBrowser = view
        qobuzWindow = popup
        popup.makeKeyAndOrderFront(nil)
        view.load(URLRequest(url: URL(string: "https://play.qobuz.com")!))
    }

    func fail(_ message: String) {
        startupTimer?.invalidate()
        let alert = NSAlert()
        alert.messageText = "TuneSaver ne peut pas continuer"
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if shuttingDown || backend?.isRunning != true { return .terminateNow }
        shuttingDown = true
        startupTimer?.invalidate()
        parentPipe?.fileHandleForWriting.closeFile()
        DispatchQueue.global().async { [weak self] in
            self?.backend?.waitUntilExit()
            DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window?.makeKeyAndOrderFront(nil)
        return true
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = AppDelegate()
application.delegate = delegate
application.run()
