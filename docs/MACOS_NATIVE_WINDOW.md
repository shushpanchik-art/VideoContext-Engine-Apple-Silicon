# Native macOS window for VideoContext Engine

The default bundle used to launch a shell script in Terminal and then open /ui in the browser. The optional native launcher embeds the existing local UI in WKWebView, detects an existing server before starting one, logs an owned server under ~/Library/Logs/VideoContext Engine/server.log, and asks for confirmation when the user presses Cmd+Q or clicks the red close button.

## Install/update without changing the VCE engine

1. Download/extract this repository branch (or use a current local checkout).
2. From its root, run:

   bash macos/install_native_window_app.command "$HOME/VideoContext-Engine"

   Replace the path with the actual project directory if needed. The installer compiles the Swift app locally, stages and validates it, then backs up the current /Applications/VideoContext Engine.app before replacing it. On update it also attempts to carry over the old .icns icon, including from dated backup bundles if the current app no longer has one. Existing desktop Finder aliases keep the same target path and therefore continue to work.

Requires Apple Silicon, macOS 11+, and Xcode Command Line Tools (xcrun swiftc). The installer does not modify VideoContextEngine_v3.19.py or user settings.

## Server lifecycle

- If http://127.0.0.1:7555/ui responds, the app connects to that server and does not claim ownership of it.
- If no UI responds, it finds the project path stored in the app bundle (then tries ~/VideoContext-Engine and ~/Downloads/VideoContext-Engine), and launches .venv/bin/python VideoContextEngine_v3.19.py directly without opening Terminal.
- On confirmed quit, an app-owned server receives SIGTERM. A server started separately is deliberately left running.
- The confirmation warns that stopping an app-owned server can interrupt an in-progress analysis.
- Diagnostic stdout/stderr is appended to ~/Library/Logs/VideoContext Engine/server.log.
- If startup fails, the same app window displays the failure and log path; choose View → Reload Page after diagnosing the problem.

The benchmark files remain a separate feature. benchmarks/manifest.example.jsonl is a template: create manual reference files and actual VCE outputs before running WER/CER; it is not expected to run unchanged.
