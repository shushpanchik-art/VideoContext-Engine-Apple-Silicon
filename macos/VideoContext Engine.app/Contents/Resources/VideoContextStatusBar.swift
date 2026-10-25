import AppKit
import Foundation

/// Persistent menu-bar companion for VideoContext Engine.
/// The Python/FastAPI engine continues to run in Terminal; quitting this icon does not stop it.
final class VideoContextStatusBar: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private var statusTextItem: NSMenuItem?
    private var refreshTimer: Timer?
    private var startupTimer: Timer?
    private var startupAttempts = 0
    private var isStartingServer = false

    private let uiURL = URL(string: "http://127.0.0.1:7555/ui")!
    private let docsURL = URL(string: "http://127.0.0.1:7555/docs")!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        installStatusItem()
        refreshServerStatus()

        refreshTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.refreshServerStatus()
        }

        // Match the previous launcher behavior: opening the app opens the UI,
        // and starts the server in Terminal if it isn't already reachable.
        if serverAvailable() {
            NSWorkspace.shared.open(uiURL)
        } else {
            startServerAndOpenUI()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        refreshTimer?.invalidate()
        startupTimer?.invalidate()
        // Do not terminate the separately running Python server.
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

        let openUI = NSMenuItem(title: "Открыть интерфейс", action: #selector(openInterface(_:)), keyEquivalent: "")
        openUI.target = self
        menu.addItem(openUI)

        let start = NSMenuItem(title: "Запустить / проверить сервер", action: #selector(startServerAndOpenUI(_:)), keyEquivalent: "")
        start.target = self
        menu.addItem(start)

        let docs = NSMenuItem(title: "Открыть API / Swagger", action: #selector(openSwagger(_:)), keyEquivalent: "")
        docs.target = self
        menu.addItem(docs)

        menu.addItem(NSMenuItem.separator())
        let quitIcon = NSMenuItem(title: "Убрать значок (сервер продолжит работать)", action: #selector(quitStatusBar(_:)), keyEquivalent: "")
        quitIcon.target = self
        menu.addItem(quitIcon)

        item.menu = menu
        statusItem = item
    }

    @objc private func openInterface(_ sender: Any? = nil) {
        if serverAvailable() {
            NSWorkspace.shared.open(uiURL)
            setStatus("Сервер работает")
        } else {
            startServerAndOpenUI()
        }
    }

    @objc private func openSwagger(_ sender: Any? = nil) {
        if serverAvailable() {
            NSWorkspace.shared.open(docsURL)
        } else {
            startServerAndOpenUI()
        }
    }

    @objc private func startServerAndOpenUI(_ sender: Any? = nil) {
        if serverAvailable() {
            setStatus("Сервер работает")
            NSWorkspace.shared.open(uiURL)
            return
        }
        guard !isStartingServer else {
            setStatus("Запуск сервера…")
            return
        }

        // Avoid starting a second engine if an older process owns the API port but /ui is broken.
        if docsAvailable() {
            setStatus("API отвечает, /ui недоступен")
            showAlert("VideoContext Engine уже отвечает на /docs, но /ui недоступен. Проверь старый сервер в Terminal; новый экземпляр не запускается, чтобы избежать конфликта порта.")
            NSWorkspace.shared.open(uiURL)
            return
        }

        guard let startScript = Bundle.main.resourceURL?.appendingPathComponent("start_server.command"),
              FileManager.default.fileExists(atPath: startScript.path) else {
            showAlert("Не найден start_server.command внутри приложения. Переустанови VideoContext Engine.")
            setStatus("Не найден скрипт запуска")
            return
        }

        do {
            try launchServerInTerminal(scriptPath: startScript.path)
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
                    NSWorkspace.shared.open(self.uiURL)
                } else if self.startupAttempts >= 120 {
                    timer.invalidate()
                    self.startupTimer = nil
                    self.isStartingServer = false
                    self.setStatus("Сервер не ответил")
                    self.showAlert("Сервер не ответил за 120 секунд. Проверь ошибки в окне Terminal.")
                }
            }
        } catch {
            isStartingServer = false
            setStatus("Не удалось запустить сервер")
            showAlert("Не удалось открыть сервер в Terminal: \(error.localizedDescription)")
        }
    }

    @objc private func quitStatusBar(_ sender: Any? = nil) {
        NSApp.terminate(nil)
    }

    private func launchServerInTerminal(scriptPath: String) throws {
        let appleScript = """
        on run argv
            set scriptPath to item 1 of argv
            tell application "Terminal"
                activate
                do script "bash " & quoted form of scriptPath
            end tell
        end run
        """

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-", scriptPath]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice

        let input = Pipe()
        process.standardInput = input
        try process.run()
        try input.fileHandleForWriting.write(contentsOf: Data(appleScript.utf8))
        try input.fileHandleForWriting.close()
        process.waitUntilExit()

        guard process.terminationStatus == 0 else {
            throw NSError(
                domain: "VideoContextStatusBar",
                code: Int(process.terminationStatus),
                userInfo: [NSLocalizedDescriptionKey: "osascript завершился с кодом \(process.terminationStatus)"]
            )
        }
    }

    private func serverAvailable() -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
        process.arguments = ["--silent", "--fail", "--max-time", "1", uiURL.absoluteString]
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
        process.arguments = ["--silent", "--fail", "--max-time", "1", docsURL.absoluteString]
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
