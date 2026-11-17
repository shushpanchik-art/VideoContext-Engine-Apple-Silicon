# Backend cleanup investigation (2026-11-17)

## Reconnaissance before modification

Baseline: main `de3d97d` (fresh clone, no existing checkout changed). All 187 tracked paths inventoried: runtime/UI, 2 vce_core files, 2 domain-profile files, 16 scripts, 12 tools, 6 macOS files, 40 test/fixture files, 28 patch/archive files, 11 workflows, 3 configs, 43 docs. Read contributor/test/distribution contracts; parsed maintained Python files and mapped all backend imports. This is whole-project structural reconnaissance and focused control-flow review, not a line-by-line correctness audit of every historical patch or empirical model-quality validation.

Runtime matches release `823eba3`; later main changes are documentation/results only. Installed runtime additionally has the recognition-language patch; its cleanup functions retain the same defect. Open PR/remote-branch inventory was checked: older backend/diagnostic branches exist, but current main is the patch base, not a remembered branch. No AGENTS.md or checkout-local skills found. Existing installation, settings, model cache, other checkouts and RAM experiments are not patch targets.

Architecture: monolithic FastAPI runtime owns ASR/OCR/VLM/Text and lifecycle; vce_core/text_reconcile is model-free; domain_profiles contains optional text normalization. Native launcher/installer launches Python and applies a bundled recognition-language patch. Bench/eval tools are separate executables, not imported by runtime. Historical patchers are explicitly archived. CI uses unittest discovery plus installer/JSON/Swift checks; real Apple Silicon inference is not implied by these checks.

## Proven defect and limits

`release_accelerator_memory` imports MLX to clear its allocator, including after PyTorch Whisper and at job-end with VLM disabled. `_vce_clear_metal_memory_caches` does likewise. MLX tracing imports MLX before a model operation and on its error path, so diagnostics can initialize/retry the failing dependency. Text finally and MLX Whisper release also actively import for cleanup. A caught failed extension import can therefore be retried by unrelated housekeeping.

Crash report identifies SIGABRT inside the installed MLX extension at module initialization. The preserved run report records nanobind duplicate DeviceType.cpu and an earlier cache-write denial. The missing original stderr prevents proving the exact initiating import failure or attributing it conclusively to the cache denial. No deliberate native crash reproduction is needed to prove the cleanup violation.

Profiles: baseline is PyTorch CPU/MPS ASR followed by optional VLM; mlx_parallel explicitly uses MLX ASR. Analysis mode controls VLM, not Text LLM, which must be disabled separately for no-MLX use. RAM+ startup preloading may legitimately load backend models and is not changed here. Shutdown cleanup must remain passive. llama.cpp paths must not initialize MLX through shared cleanup.

## Minimal fix boundary

Only cleanup and diagnostic observation use already-loaded, fully initialized modules; missing/in-progress modules are skipped. Explicit model work retains imports, device configuration, synchronization, backend selection and error handling. Retain job-end VLM unload: a model cached by a preceding RAM+ job must still be released when switching to RAM−, even if this job disables VLM. No blanket backend disablement, dependency update, security change, or automatic retry/reset of an aborting process. This does not claim to cure an MLX/native initializer bug or prevent every deliberate subsequent model-load retry after a failed initialization.

## Verification plan

Baseline suite: 278 unittest tests passed before changes. Add executable extracted-function tests that reject backend imports, repeat cleanup after simulated initialization failure, preserve loaded cache release/MPS behavior, skip initializing modules, preserve trace's original exception and test retained RAM− final cleanup. Run full tests and installer checks on final patch; exercise recognition-language patch in an isolated temporary copy. Real video, Metal and RAM performance acceptance remain separate and must not be claimed.

## Verified result

- Before patch: 278 tests passed. Final full suite: 292 tests passed on Python 3.11.18.
- 14 new extracted-function tests execute unchanged function bodies with stub modules and a backend-import rejection hook. They cover absent/initializing/removed-after-failure modules, repeated cleanup, current and legacy MLX cache APIs, MPS synchronize failure, cleanup failure, diagnostic success/failure/cancellation, Text finally, MLX Whisper holder ownership, lifespan shutdown, RAM+ cached model release by a RAM− job with VLM disabled.
- Bundled recognition-language patch applied in memory: exact baseline+patch equals the installed runtime. On modified runtime it remains idempotent; all 14 new cleanup tests pass again against the language-patched AST. No installed files were changed.
- Shell syntax for the three documented installer/launcher scripts, both documented JSON manifests, and `git diff --check` pass.
- Independent read-only review found no blocking defects. No dependency change, native backend import, inference, installer execution, user-settings modification or experiment-process interruption occurred.

Not run: native Swift compile (unchanged), real complete video requests across CPU/MPS/MLX/llama.cpp, Metal allocation/performance tests, live cancellation and RAM matrix. AST tests are deliberately model-free and do not establish end-to-end output quality or GPU stability. Before deploying, run short isolated whisper_ocr and ocr_only requests with Text LLM off, assert valid final artifacts and zero MLX imports, then explicitly enabled MLX smoke/cleanup and RAM+→RAM− lifecycle acceptance. Do not interpret the no-import fix as repairing the underlying native initializer or permitting repeated deliberate imports after a failure.

## Isolated acceptance, 2026-11-17

Performed after the owner explicitly authorized testing during active host use. The tracked branch was exported to a private temporary directory with independent settings, cwd, TMPDIR and a loopback-only server on a separate port. The installed runtime/settings were not changed. Existing Python 3.11.18 dependencies and cached Whisper medium were reused; CPU thread count was limited to one. No model downloads, user video, or production server were used. An initial harness configuration typo (`mlx` instead of the declared `mlx_metal`) was corrected before acceptance; it was a harness startup failure, not a product defect.

- Input: synthetic six-second 640×360 H.264 video at 5 fps, printed `BLUE DOOR TEST 123`, and synthesized speech `The blue door is open. This is a short video test.`
- Real HTTP `ocr_only`: 200; expected OCR text recovered; output TXT and JSON exist in the isolated upload cwd and contain the expected text.
- Real HTTP `whisper_ocr`: 200; cached medium ran on CPU with acceleration disabled; expected speech recovered verbatim and expected OCR recovered. TXT and JSON verified by reading their contents.
- Both: baseline_mps_sequential, RAM−, VLM disabled by analysis mode, Text disabled, summary disabled. Blocking/journaling hooks on direct and dynamic MLX-family imports installed before runtime import. Initial atexit-only report was not reliable under signal termination; a subsequent observed-lifespan + normal-exit run below verifies `attempts=[]`, `loaded=[]`.
- Server completed application shutdown. Python emitted a warning about one leaked multiprocessing semaphore; subsequent controlled investigation below traces this warning to signal termination of a tqdm lock; it is not evidence of MLX initialization or SIGABRT.
- Separate short-lived native smoke: actual MLX CPU and GPU evaluation of `[1,2,3] * 2` each produced `[2,4,6]`; the real extracted cleanup function bodies ran against loaded MLX successfully. Exit 0, stderr empty. No model inference or deliberate failed-import retry was performed in this smoke.

The host was active and swapping before/during the test. These runs establish bounded functional acceptance only; their durations must not be used as comparative performance evidence. A full real-model MLX/VLM pipeline, live cancellation, RAM+→RAM− hardware lifecycle, broad backend matrix, and long-run/native initialization stability remain unverified. PR remains draft pending that broader acceptance decision; do not interpret one native smoke as a fix for every native initializer failure.


## Semaphore warning investigation

Instrumented `multiprocessing.resource_tracker.register/unregister` with stack capture in isolated subprocesses. Compared baseline de3d97d against the patch, keeping the same dependency environment, synthetic input, CPU settings and MLX-import rejection hooks. Those hooks intentionally prevent baseline cleanup from reaching native MLX and are a stated control, not an unmodified baseline-native stability claim.

- Idle startup + SIGTERM shutdown: baseline 2/2 and patched 2/2 without semaphore warnings.
- OCR-only + SIGTERM: baseline and patched each HTTP 200, no semaphore warning.
- Whisper+OCR + SIGTERM: baseline and patched each HTTP 200, each the same one-semaphore warning.
- Registration stack: `whisper.transcribe` line 264 → `tqdm.tqdm` → `TqdmDefaultWriteLock.create_mp_lock` → `multiprocessing.RLock` → `SemLock` registration. No unregister under signal termination.
- Installed Uvicorn `Server.capture_signals` restores handlers and re-raises the captured signal after application shutdown. Thus application lifespan completion is not equivalent to normal Python interpreter finalization.
- Minimal independent reproduction, no VCE/MLX/Whisper model: `tqdm.get_lock()` followed by normal interpreter exit yields exit 0 without warning; the same followed by SIGTERM yields exit -15 and the same one-semaphore warning.
- Patched real Whisper+OCR repeated with the test server stopped via `server.should_exit=True`, not a process signal: HTTP 200, expected OCR and speech, exit 0, semaphore registration AND matching unregister, no semaphore warning. A report written after the actual lifespan cleanup verifies zero attempted MLX-family imports and zero loaded MLX-family modules. This also corrects the initial atexit-only reporting weakness under signal termination.

Conclusion limited to this warning: it is reproducible from tqdm's multiprocessing lock plus the signal-exit path, already present on baseline, rather than a regression introduced by the backend-cleanup patch. No production shutdown code, lock internals or dependencies were changed. This does not establish behavior for every launcher shutdown route or long-running process.
