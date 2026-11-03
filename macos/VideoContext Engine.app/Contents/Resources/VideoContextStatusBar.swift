import AppKit
import Darwin
import Foundation
import WebKit

// VCE_MANAGED_ENGINE_PROCESS_LIFECYCLE_V1

/// Standalone VideoContext Engine window plus a persistent menu-bar companion.
/// The Python/FastAPI server runs without Terminal, with stdout/stderr appended to a log file.
final class VideoContextStatusBar: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private var statusTextItem: NSMenuItem?
    private var window: NSWindow?
    private var webView: WKWebView?
    private var refreshTimer: Timer?
    private var startupTimer: Timer?
    private var startupAttempts = 0
    private var isStartingServer = false
    private var serverProcess: Process?
    private var serverLogHandle: FileHandle?
    private var serverLogURL: URL?

    private enum MainPage {
        case ui
        case docs

        var urlString: String {
            switch self {
            case .ui: return "http://127.0.0.1:7555/ui"
            case .docs: return "http://127.0.0.1:7555/docs"
            }
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // A previous app instance may have been force-quit or crashed, leaving
        // its Python engine orphaned. Purge only this project's runtime before
        // checking port 7555, so the new app never attaches to an old server.
        _ = terminateProjectEngineProcesses(reason: "application launch")

        // This must be a regular app so it has a real window and Dock presence.
        NSApp.setActivationPolicy(.regular)
        createMainWindow()
        installStatusItem()
        refreshServerStatus()

        refreshTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.refreshServerStatus()
        }

        if serverAvailable() {
            showMainWindow(page: .ui)
        } else {
            startServerAndOpenUI()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        refreshTimer?.invalidate()
        startupTimer?.invalidate()

        // The launcher uses exec, so its Process PID becomes the Python server
        // PID. Signal the managed process, then scan the project path as a
        // second line of defense for older/orphaned VCE processes.
        if let process = serverProcess, process.isRunning {
            process.terminate()
        }
        _ = terminateProjectEngineProcesses(reason: "application termination")
        try? serverLogHandle?.close()
        serverLogHandle = nil
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if serverAvailable() {
            showMainWindow(page: .ui)
        } else {
            startServerAndOpenUI()
        }
        return true
    }

    // Closing the main window means closing the app and its local engine.
    // Reopening the app performs a fresh stale-process sweep before startup.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    private func configuredProjectDirectory() -> String? {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let pathFile = home
            .appendingPathComponent("Library/Application Support/VideoContext Engine/project_path")
        if let saved = try? String(contentsOf: pathFile, encoding: .utf8) {
            let candidate = saved.trimmingCharacters(in: .whitespacesAndNewlines)
            let engine = URL(fileURLWithPath: candidate)
                .appendingPathComponent("VideoContextEngine_v3.19.py").path
            if !candidate.isEmpty && FileManager.default.fileExists(atPath: engine) {
                return URL(fileURLWithPath: candidate).standardizedFileURL.path
            }
        }

        // Compatibility fallback for installations created before project_path
        // was persisted by the native installer.
        let candidates = [
            home.appendingPathComponent("VideoContext-Engine-Apple-Silicon"),
            home.appendingPathComponent("VideoContext-Engine"),
            home.appendingPathComponent("Downloads/VideoContext-Engine")
        ]
        for candidate in candidates {
            let engine = candidate.appendingPathComponent("VideoContextEngine_v3.19.py")
            if FileManager.default.fileExists(atPath: engine.path) {
                return candidate.standardizedFileURL.path
            }
        }
        NSLog("[VCE lifecycle] Project path not found; no Python process was signalled.")
        return nil
    }

    private func matchingProjectEnginePIDs(projectDirectory: String) -> [pid_t] {
        let enginePath = URL(fileURLWithPath: projectDirectory)
            .appendingPathComponent("VideoContextEngine_v3.19.py").standardizedFileURL.path
        let ps = Process()
        ps.executableURL = URL(fileURLWithPath: "/bin/ps")
        ps.arguments = ["-axo", "pid=,command="]
        let pipe = Pipe()
        ps.standardOutput = pipe
        ps.standardError = FileHandle.nullDevice

        do {
            try ps.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            ps.waitUntilExit()
            guard ps.terminationStatus == 0 else {
                NSLog("[VCE lifecycle] Could not enumerate processes with ps.")
                return []
            }

            let output = String(data: data, encoding: .utf8) ?? ""
            var matches: [pid_t] = []
            for line in output.split(whereSeparator: \.isNewline) {
                let fields = line.split(
                    maxSplits: 1,
                    omittingEmptySubsequences: true,
                    whereSeparator: { $0.isWhitespace }
                )
                guard fields.count == 2,
                      let pid = Int32(String(fields[0])),
                      pid != getpid() else { continue }
                // Match the exact engine script path. Other Python applications
                // and other VideoContext clones are deliberately left untouched.
                if String(fields[1]).contains(enginePath) {
                    matches.append(pid)
                }
            }
            return Array(Set(matches)).sorted()
        } catch {
            NSLog("[VCE lifecycle] Process scan failed: %@", error.localizedDescription)
            return []
        }
    }

    @discardableResult
    private func terminateProjectEngineProcesses(reason: String) -> Int {
        guard let projectDirectory = configuredProjectDirectory() else { return 0 }
        let initial = matchingProjectEnginePIDs(projectDirectory: projectDirectory)
        guard !initial.isEmpty else {
            NSLog("[VCE lifecycle] No previous engine processes found (%@).", reason)
            return 0
        }

        NSLog("[VCE lifecycle] Sending SIGTERM to %d project engine process(es) (%@): %@",
              initial.count, reason, initial.map { String($0) }.joined(separator: ", "))
        for pid in initial {
            if matchingProjectEnginePIDs(projectDirectory: projectDirectory).contains(pid) {
                _ = Darwin.kill(pid, SIGTERM)
            }
        }

        // Give FastAPI/Python a brief graceful-shutdown window. A model call
        // blocked in Metal may not return, so enforce a hard timeout below.
        let gracefulDeadline = Date().addingTimeInterval(2.5)
        while Date() < gracefulDeadline {
            if matchingProjectEnginePIDs(projectDirectory: projectDirectory).isEmpty {
                NSLog("[VCE lifecycle] All project engine processes stopped gracefully.")
                return initial.count
            }
            Thread.sleep(forTimeInterval: 0.1)
        }

        let survivors = matchingProjectEnginePIDs(projectDirectory: projectDirectory)
        if !survivors.isEmpty {
            NSLog("[VCE lifecycle] SIGKILL for %d unresponsive engine process(es): %@",
                  survivors.count, survivors.map { String($0) }.joined(separator: ", "))
            for pid in survivors {
                if matchingProjectEnginePIDs(projectDirectory: projectDirectory).contains(pid) {
                    _ = Darwin.kill(pid, SIGKILL)
                }
            }
        }

        let finalDeadline = Date().addingTimeInterval(1.0)
        while Date() < finalDeadline &&
              !matchingProjectEnginePIDs(projectDirectory: projectDirectory).isEmpty {
            Thread.sleep(forTimeInterval: 0.1)
        }
        let remaining = matchingProjectEnginePIDs(projectDirectory: projectDirectory)
        if !remaining.isEmpty {
            NSLog("[VCE lifecycle] WARNING: engine process(es) still visible after SIGKILL: %@",
                  remaining.map { String($0) }.joined(separator: ", "))
        } else {
            NSLog("[VCE lifecycle] Project engine process cleanup complete.")
        }
        return initial.count - remaining.count
    }

    private func createMainWindow() {
        let frame = NSRect(x: 0, y: 0, width: 1180, height: 800)
        let window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "VideoContext Engine"
        window.minSize = NSSize(width: 760, height: 540)
        window.center()

        let webView = WKWebView(frame: frame)
        webView.autoresizingMask = [.width, .height]
        window.contentView = webView

        self.window = window
        self.webView = webView
    }

    private func showMainWindow(page: MainPage, forceReload: Bool = false) {
        guard let window = window,
              let webView = webView,
              let targetURL = URL(string: page.urlString) else { return }
        NSApp.setActivationPolicy(.regular)

        // Construct the URL on demand instead of retaining URL objects as
        // long-lived delegate properties or passing them through timer callbacks.
        // The crash report from the startup timer entered this method with an
        // invalid object being retained; a page enum keeps that boundary value-only.
        let currentURL = webView.url
        let alreadyShowingRequestedPage =
            currentURL?.scheme == targetURL.scheme &&
            currentURL?.host == targetURL.host &&
            currentURL?.port == targetURL.port &&
            currentURL?.path == targetURL.path

        if forceReload || !alreadyShowingRequestedPage {
            webView.load(URLRequest(url: targetURL))
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func installStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.toolTip = "VideoContext Engine"

        if let iconURL = Bundle.main.resourceURL?.appendingPathComponent("VideoContextEngine.icns"),
           let image = NSImage(contentsOf: iconURL) {
            image.size = NSSize(width: 18, height: 18)
            image.isTemplate = false
            item.button?.image = image
        } else if let image = NSImage(systemSymbolName: "film", accessibilityDescription: "VideoContext Engine") {
            image.isTemplate = true
            item.button?.image = image
        } else {
            item.button?.title = "VCE"
        }

        let menu = NSMenu()

        let status = NSMenuItem(title: "Сервер: проверка…", action: nil, keyEquivalent: "")
        status.isEnabled = false
        menu.addItem(status)
        statusTextItem = status
        menu.addItem(NSMenuItem.separator())

        let openUI = NSMenuItem(title: "Открыть VideoContext Engine", action: #selector(openInterface(_:)), keyEquivalent: "")
        openUI.target = self
        menu.addItem(openUI)

        let reloadUI = NSMenuItem(title: "Перезагрузить интерфейс", action: #selector(reloadInterface(_:)), keyEquivalent: "")
        reloadUI.target = self
        menu.addItem(reloadUI)

        let start = NSMenuItem(title: "Запустить / проверить сервер", action: #selector(startServerAndOpenUI(_:)), keyEquivalent: "")
        start.target = self
        menu.addItem(start)

        let docs = NSMenuItem(title: "Открыть API / Swagger", action: #selector(openSwagger(_:)), keyEquivalent: "")
        docs.target = self
        menu.addItem(docs)

        menu.addItem(NSMenuItem.separator())
        let quitIcon = NSMenuItem(title: "Закрыть приложение и остановить сервер", action: #selector(quitStatusBar(_:)), keyEquivalent: "")
        quitIcon.target = self
        menu.addItem(quitIcon)

        item.menu = menu
        statusItem = item
    }

    @objc private func openInterface(_ sender: Any? = nil) {
        if serverAvailable() {
            setStatus("Сервер работает")
            showMainWindow(page: .ui)
        } else {
            startServerAndOpenUI()
        }
    }

    @objc private func reloadInterface(_ sender: Any? = nil) {
        if serverAvailable() {
            setStatus("Сервер работает")
            // This action is intentionally explicit: reloading can reset the
            // in-memory file queue and progress held by the page.
            showMainWindow(page: .ui, forceReload: true)
        } else {
            startServerAndOpenUI()
        }
    }

    @objc private func openSwagger(_ sender: Any? = nil) {
        if serverAvailable() {
            showMainWindow(page: .docs)
        } else {
            startServerAndOpenUI()
        }
    }

    @objc private func startServerAndOpenUI(_ sender: Any? = nil) {
        if serverAvailable() {
            setStatus("Сервер работает")
            showMainWindow(page: .ui)
            return
        }
        guard !isStartingServer else {
            setStatus("Запуск сервера…")
            return
        }

        // Avoid starting a second engine if an older process owns the API port but /ui is broken.
        if docsAvailable() {
            setStatus("API отвечает, /ui недоступен")
            showMainWindow(page: .ui)
            showAlert("VideoContext Engine уже отвечает на /docs, но /ui недоступен. Проверь журнал ~/Library/Logs/VideoContext Engine/server.log; новый экземпляр не запускается, чтобы избежать конфликта порта.")
            return
        }

        guard let startScript = Bundle.main.resourceURL?.appendingPathComponent("start_server.command"),
              FileManager.default.fileExists(atPath: startScript.path) else {
            showAlert("Не найден start_server.command внутри приложения.")
            setStatus("Не найден скрипт запуска")
            return
        }

        do {
            try launchServerInBackground(scriptPath: startScript.path)
            isStartingServer = true
            startupAttempts = 0
            setStatus("Запуск сервера…")
            startupTimer?.invalidate()
            startupTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] timer in
                guard let self = self else {
                    timer.invalidate()
                    return
                }
                self.startupAttempts += 1
                if self.serverAvailable() {
                    timer.invalidate()
                    self.startupTimer = nil
                    self.isStartingServer = false
                    self.setStatus("Сервер работает")
                    // We just started the server; force navigation in case the
                    // WKWebView was left on an error page from an earlier run.
                    self.showMainWindow(page: .ui, forceReload: true)
                } else if self.serverProcess == nil && self.startupAttempts >= 3 {
                    timer.invalidate()
                    self.startupTimer = nil
                    self.isStartingServer = false
                    self.setStatus("Процесс сервера завершился")
                    self.showAlert("Сервер завершился до запуска интерфейса. Проверь журнал: ~/Library/Logs/VideoContext Engine/server.log")
                } else if self.startupAttempts >= 120 {
                    timer.invalidate()
                    self.startupTimer = nil
                    self.isStartingServer = false
                    self.setStatus("Сервер не ответил")
                    self.showAlert("Сервер не ответил за 120 секунд. Посмотри журнал сервера: ~/Library/Logs/VideoContext Engine/server.log")
                }
            }
        } catch {
            isStartingServer = false
            setStatus("Не удалось запустить сервер")
            showAlert("Не удалось запустить локальный сервер: \(error.localizedDescription)")
        }
    }

    @objc private func quitStatusBar(_ sender: Any? = nil) {
        NSApp.terminate(nil)
    }

    /// Launch the local engine directly from the app, without opening Terminal.
    /// start_server.command execs Python so this Process owns the actual server PID.
    private func launchServerInBackground(scriptPath: String) throws {
        let logDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/VideoContext Engine", isDirectory: true)
        try FileManager.default.createDirectory(
            at: logDirectory,
            withIntermediateDirectories: true
        )
        let logURL = logDirectory.appendingPathComponent("server.log")
        if !FileManager.default.fileExists(atPath: logURL.path) {
            guard FileManager.default.createFile(atPath: logURL.path, contents: Data()) else {
                throw NSError(
                    domain: "VideoContextStatusBar",
                    code: 10,
                    userInfo: [NSLocalizedDescriptionKey: "Не удалось создать журнал: \(logURL.path)"]
                )
            }
        }
        guard let logHandle = FileHandle(forWritingAtPath: logURL.path) else {
            throw NSError(
                domain: "VideoContextStatusBar",
                code: 11,
                userInfo: [NSLocalizedDescriptionKey: "Не удалось открыть журнал: \(logURL.path)"]
            )
        }
        try logHandle.seekToEnd()

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [scriptPath]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = logHandle
        process.standardError = logHandle
        process.environment = ProcessInfo.processInfo.environment.merging([
            "PYTHONUNBUFFERED": "1"
        ]) { _, new in new }

        process.terminationHandler = { [weak self] child in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if self.serverProcess === child {
                    self.serverProcess = nil
                    try? self.serverLogHandle?.close()
                    self.serverLogHandle = nil
                    self.isStartingServer = false
                    self.refreshServerStatus()
                    if child.terminationStatus != 0 {
                        self.setStatus("сервер завершился с кодом \(child.terminationStatus)")
                    }
                }
            }
        }

        try process.run()
        self.serverProcess = process
        self.serverLogHandle = logHandle
        self.serverLogURL = logURL
    }

    private func serverAvailable() -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
        process.arguments = ["--silent", "--fail", "--max-time", "1", MainPage.ui.urlString]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus == 0
        } catch {
            return false
        }
    }

    private func docsAvailable() -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
        process.arguments = ["--silent", "--fail", "--max-time", "1", MainPage.docs.urlString]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus == 0
        } catch {
            return false
        }
    }

    private func refreshServerStatus() {
        if serverAvailable() {
            setStatus("Сервер работает")
        } else if docsAvailable() {
            setStatus("API работает, /ui недоступен")
        } else {
            setStatus("Сервер не запущен")
        }
    }

    private func setStatus(_ text: String) {
        statusTextItem?.title = "Сервер: \(text)"
        statusItem?.button?.toolTip = "VideoContext Engine — \(text)"
    }

    private func showAlert(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "VideoContext Engine"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}

let application = NSApplication.shared
let delegate = VideoContextStatusBar()
application.delegate = delegate
application.run()
