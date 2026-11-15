# User workflow rules for VideoContext Engine work

These rules are mandatory for every future agent/session working on this project.

1. **Full-video acceptance only.** The required acceptance input is exactly:
   `/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4`
   Do not use trimmed clips or short excerpts as acceptance tests. Synthetic/unit fixtures are permitted only as supplementary tests. If the source cannot be accessed, report that plainly and do not substitute another video.
2. **Mac Terminal commands.** Give copy/paste-ready commands for macOS Terminal to download and run scripts directly from GitHub. Do not give Linux-only instructions or vague GUI steps when a Terminal command is possible.
3. **Automatic evidence upload.** Every full test runner must create a timestamped evidence directory, a compact JSON summary, full logs, checksums, process/resource samples and output-quality artifacts. At the end it must automatically upload the evidence archive to the private GitHub staging repository or the user's explicitly configured Dropbox destination.
4. **Known destination.** The script must print the exact repository release URL or Dropbox path, save it in `UPLOAD_RECEIPT.txt`, and use a predictable naming convention. The agent must know where to look without asking the user to find or manually attach files.
5. **Large files.** When the full local project or test evidence is too large for Git commits, upload it as a GitHub Release asset in the private staging repository or as a Dropbox file. Do not commit large archives, models or videos into the public source repository.
6. **No secret leakage.** Before upload, exclude model caches, virtualenvs, source media and generated personal content unless explicitly required; recursively redact credentials, tokens, cookies and private settings. Prefer private staging. Never upload to public `main` evidence containing local machine data.
7. **Reproducibility.** Record source and runtime SHA-256, git commit/status/diff, dependency versions, app/runtime paths, exact request configuration, model/backend/device, stage timings, memory/swap, request ID, process tree, HTTP result and output paths.
8. **One request, attributable logs.** For a diagnostic run, send exactly one analysis request unless the user explicitly asks otherwise. Correlate logs to that request and PID tree; distinguish UI polling, prior jobs, user cancellation and new request events.
9. **Do not overclaim.** A curl exit code of 0 is not an HTTP success. CI, syntax checks and synthetic tests do not prove hardware behavior. Report missing evidence and failed stages explicitly.
10. **Preserve working features.** Do not remove Apple Vision OCR caching, multi-language recognition selection, or project-scoped stale VCE process cleanup while changing inference backends.
11. **Safe process cleanup.** Never kill every Python process or any process merely because it listens on port 7555. Verify canonical project path and process ownership/ancestry, prefer SIGTERM, and escalate only for confirmed VCE orphans.
12. **No destructive edits in place.** Back up the installed runtime and settings, validate candidate source, compile, and provide rollback before applying changes.
