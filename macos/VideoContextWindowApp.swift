import AppKit
import Foundation
import WebKit

@MainActor
final class VideoContextAppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate {
    private let uiURL = URL(string: "http://127.0.0.1:7555/ui")!
    private var window: NSWindow!
    private var webView: WKWebView!
    private var serverProcess: Process?
    private var ownsServer = false
    private var quitConfirmed = false
    private var showingStatusPage = false
    private var logHandle: FileHandle?
    private var logURL: URL?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        configureMainMenu()
        createWindow()
        showStatusPage(title: "Запуск VideoContext Engine", detail: "Подключаемся к локальному движку на 127.0.0.1:7555…")
        Task { await startOrConnectToEngine() }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitConfirmed {
            stopOwnedServer()
            return .terminateNow
        }
        guard confirmQuit() else { return .terminateCancel }
        quitConfirmed = true
        stopOwnedServer()
        return .terminateNow
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { window.makeKeyAndOrderFront(nil) }
        NSApp.activate(ignoringOtherApps: true)
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        stopOwnedServer()
        try? logHandle?.close()
        logHandle = nil
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if quitConfirmed { return true }
        guard confirmQuit() else { return false }
        quitConfirmed = true
        DispatchQueue.main.async { NSApp.terminate(nil) }
        return false
    }

    nonisolated func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let message = error.localizedDescription
        Task { @MainActor [weak self] in
            guard let self, !self.showingStatusPage else { return }
            self.showStatusPage(title: "Не удалось открыть интерфейс", detail: "Локальный сервер пока не отвечает: \(message)")
        }
    }

    @objc private func reloadPage() {
        if showingStatusPage {
            Task { await startOrConnectToEngine() }
        } else {
            webView.reload()
        }
    }

    private func createWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "VideoContext Engine"
        window.minSize = NSSize(width: 900, height: 600)
        window.center()
        window.delegate = self

        let configuration = WKWebViewConfiguration()
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func configureMainMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)

        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(
            title: "О VideoContext Engine",
            action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
            keyEquivalent: ""
        ))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(
            title: "Скрыть VideoContext Engine",
            action: #selector(NSApplication.hide(_:)),
            keyEquivalent: "h"
        ))
        appMenu.addItem(NSMenuItem(
            title: "Закрыть VideoContext Engine…",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        ))
        appMenu.items.last?.keyEquivalentModifierMask = [.command]
        appMenuItem.submenu = appMenu

        let viewMenuItem = NSMenuItem()
        mainMenu.addItem(viewMenuItem)
        let viewMenu = NSMenu(title: "Вид")
        viewMenu.addItem(NSMenuItem(title: "Перезагрузить страницу", action: #selector(reloadPage), keyEquivalent: "r"))
        viewMenuItem.submenu = viewMenu
        NSApp.mainMenu = mainMenu
    }

    private func confirmQuit() -> Bool {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Точно закрыть VideoContext Engine?"
        alert.informativeText = """
        Если приложение запустило движок, выход остановит этот сервер и может прервать текущий анализ.
        Если сервер был запущен отдельно, например из Terminal, он продолжит работать.
        """
        alert.addButton(withTitle: "Не закрывать")
        alert.addButton(withTitle: "Закрыть приложение")
        return alert.runModal() == .alertSecondButtonReturn
    }

    private func projectCandidates() -> [URL] {
        var candidates: [URL] = []
        if let file = Bundle.main.url(forResource: "project-path", withExtension: "txt"),
           let savedPath = try? String(contentsOf: file, encoding: .utf8) {
            let path = savedPath.trimmingCharacters(in: .whitespacesAndNewlines)
            if !path.isEmpty { candidates.append(URL(fileURLWithPath: path, isDirectory: true)) }
        }
        let home = FileManager.default.homeDirectoryForCurrentUser
        candidates.append(home.appendingPathComponent("VideoContext-Engine", isDirectory: true))
        candidates.append(home.appendingPathComponent("Downloads/VideoContext-Engine", isDirectory: true))
        candidates.append(home.appendingPathComponent("Downloads/VideoContext-Engine-Apple-Silicon", isDirectory: true))
        var seen = Set<String>()
        return candidates.filter { seen.insert($0.standardizedFileURL.path).inserted }
    }

    private func findProject() -> URL? {
        let fm = FileManager.default
        return projectCandidates().first { project in
            fm.isExecutableFile(atPath: project.appendingPathComponent(".venv/bin/python").path)
                && fm.fileExists(atPath: project.appendingPathComponent("VideoContextEngine_v3.19.py").path)
        }
    }

    private func serverResponds() async -> Bool {
        await withCheckedContinuation { continuation in
            var request = URLRequest(url: uiURL)
            request.httpMethod = "GET"
            request.timeoutInterval = 1.5
            URLSession.shared.dataTask(with: request) { _, response, error in
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                continuation.resume(returning: error == nil && (200..<300).contains(status))
            }.resume()
        }
    }

    private func startOrConnectToEngine() async {
        if await serverResponds() {
            loadUI()
            return
        }

        guard let project = findProject() else {
            let paths = projectCandidates().map(\.path).joined(separator: "\n")
            showStatusPage(
                title: "Не найден проект VideoContext Engine",
                detail: """
                Не найдена папка проекта с .venv/bin/python и VideoContextEngine_v3.19.py.
                Проверь один из путей ниже и запусти установщик окна ещё раз с правильным путём:
                \(paths)
                """
            )
            return
        }

        do {
            let process = try makeEngineProcess(project: project)
            try process.run()
            serverProcess = process
            ownsServer = true

            for _ in 0..<180 {
                if await serverResponds() {
                    loadUI()
                    return
                }
                if !process.isRunning { break }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }

            showStatusPage(
                title: "Не удалось запустить движок",
                detail: """
                Не удалось подключиться к интерфейсу за 180 секунд.
                Проект: \(project.path)
                Лог сервера: \(logURL?.path ?? "недоступен")
                Проверь лог и пункт «Вид → Перезагрузить страницу».
                """
            )
        } catch {
            showStatusPage(
                title: "Ошибка запуска движка",
                detail: "\(error.localizedDescription)\n\nЛог: \(logURL?.path ?? "не создан")"
            )
        }
    }

    private func makeEngineProcess(project: URL) throws -> Process {
        let python = project.appendingPathComponent(".venv/bin/python")
        let engine = project.appendingPathComponent("VideoContextEngine_v3.19.py")
        guard FileManager.default.isExecutableFile(atPath: python.path),
              FileManager.default.fileExists(atPath: engine.path) else {
            throw NSError(domain: "VideoContextLauncher", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "В проекте отсутствует исполняемый .venv/bin/python или VideoContextEngine_v3.19.py."
            ])
        }

        let logsDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/VideoContext Engine", isDirectory: true)
        try FileManager.default.createDirectory(at: logsDirectory, withIntermediateDirectories: true)
        let log = logsDirectory.appendingPathComponent("server.log")
        if !FileManager.default.fileExists(atPath: log.path) {
            FileManager.default.createFile(atPath: log.path, contents: Data())
        }
        let handle = try FileHandle(forWritingTo: log)
        try handle.seekToEnd()
        logHandle = handle
        logURL = log

        var environment = ProcessInfo.processInfo.environment
        let stablePath = "/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PATH"] = stablePath + ":" + (environment["PATH"] ?? "")
        environment["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        environment["TOKENIZERS_PARALLELISM"] = "false"
        if let cert = runAndCapture(python: python, args: ["-m", "certifi"], directory: project),
           !cert.isEmpty, FileManager.default.fileExists(atPath: cert) {
            environment["SSL_CERT_FILE"] = cert
            environment["REQUESTS_CA_BUNDLE"] = cert
        }

        let process = Process()
        process.executableURL = python
        process.arguments = [engine.path]
        process.currentDirectoryURL = project
        process.environment = environment
        process.standardOutput = handle
        process.standardError = handle
        return process
    }

    private func runAndCapture(python: URL, args: [String], directory: URL) -> String? {
        let helper = Process()
        let pipe = Pipe()
        helper.executableURL = python
        helper.arguments = args
        helper.currentDirectoryURL = directory
        helper.standardOutput = pipe
        helper.standardError = FileHandle.nullDevice
        do {
            try helper.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            helper.waitUntilExit()
            guard helper.terminationStatus == 0 else { return nil }
            return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        } catch {
            return nil
        }
    }

    private func loadUI() {
        showingStatusPage = false
        webView.load(URLRequest(url: uiURL))
    }

    private func showStatusPage(title: String, detail: String) {
        showingStatusPage = true
        let escapedTitle = title
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
        let escapedDetail = detail
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
        let html = """
        <!doctype html>
        <html lang="ru"><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <body style="font:16px -apple-system,BlinkMacSystemFont,sans-serif;background:#f5f5f7;color:#202124;margin:0;padding:52px;line-height:1.5">
          <main style="max-width:900px;margin:0 auto;background:white;border-radius:18px;padding:32px;box-shadow:0 6px 30px #0001">
            <h1 style="font-size:26px">\(escapedTitle)</h1>
            <p style="white-space:pre-wrap">\(escapedDetail)</p>
            <p>Попробуй выбрать <b>Вид → Перезагрузить страницу</b> после проверки лога.</p>
            <p style="color:#666">Окно приложения можно закрыть через Cmd+Q; перед выходом появится подтверждение.</p>
          </main>
        </body></html>
        """
        webView.loadHTMLString(html, baseURL: uiURL)
    }

    private func stopOwnedServer() {
        guard ownsServer, let process = serverProcess, process.isRunning else { return }
        process.terminate()
        ownsServer = false
    }
}

@MainActor
@main
struct VideoContextWindowMain {
    static func main() {
        let application = NSApplication.shared
        let delegate = VideoContextAppDelegate()
        application.delegate = delegate
        application.setActivationPolicy(.regular)
        application.run()
    }
}
