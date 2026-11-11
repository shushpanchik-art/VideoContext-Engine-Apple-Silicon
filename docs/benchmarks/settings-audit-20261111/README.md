# Settings audit: completed stage evidence (interim)

Evidence snapshot: 2026-11-11 00:01:25 UTC. This is not a completed settings audit or a new default preset. Only sanitized measurements and conclusions are published. Raw media, frames, transcripts, OCR text, generated reports, local paths and customer identifiers remain private.

## Provenance and scope

- Installed runtime SHA-256: `8aa39daa7815ccdd9e6656cb05aa1fe0d5a642fe101b56787a3c2c3d3a52eac7`.
- Distribution marker: `823eba3b05622baa253138144e71aaa24d6a7f6f`. The installed application is not a Git checkout and includes installer modifications. The marker alone does not identify its exact source; the runtime digest does.
- Four fixed private inputs are anonymized consistently as Video A/B/C/D. The longest is Video D. There is no public input corpus, so independent reproduction requires equivalent private inputs and the exact installed runtime.
- This publication changes documentation only. It does not change the running application or saved settings. Private evidence is not copied wholesale into the repository.
- The settings inventory contains 61 parameters. An inventory is not proof of exhaustive quality coverage; earlier gap lists predate these completed stages.

## Scene threshold stage: complete

Unchanged installed detector extracted by AST; four videos, two thresholds (0.15 and 0.172), three repetitions each, alternating AB/BA/AB order: 24 detector calls. All scene boundaries were stable within each condition. Keyframe metadata was attached only to the first repetition and is not part of the repeated-boundary comparison.

- Video A: 12 scenes at 0.15, 11 at 0.172.
- Video B: 7 at both thresholds.
- Video C: 9 at both thresholds.
- Video D: 90 at both thresholds.

The lower threshold is not universally better. Additional segmentation alone does not prove better semantic coverage or end-to-end report quality. Timings in the JSON are detector-only, not full pipeline latency. Separately extracted keyframe previews are not repeated inference trials.

## OCR stage: complete within sparse-frame scope

75 calls across five fixed-time frames from four videos, three repetitions per condition. Manual review used original frame pixels rather than substring matching. Apple accurate at confidence 0.1 and 0.3 produced identical text on all tested frames; 0.5 removed some valid content as well as noise. Retaining accurate/0.3 remains defensible for this panel, not globally optimal.

All 15 fast-mode calls failed before recognition because the fast language list did not support Russian. These are compatibility failures, not successful faster OCR. Tesseract supplied no additional verified fact on this sparse panel. Existing OFF status is not overturned; full-video ON/OFF/downstream benefit remains unproven.

Both OCR pipelines repeat their errors. Observed failure categories include table reading order, loss of row/value binding through deduplication, column interleaving and noisy background recognition. The review does not publish source-specific facts.

Apple receives original pixels; Tesseract receives the application's 1600-pixel/JPEG preparation. This compares configured pipelines, not matched-raster intrinsic engine accuracy. A brief read-only preview extraction overlapped part of the OCR stage, so timings are descriptive rather than isolated microbenchmark precision.

## Long-context contract stage: complete, not ML quality

The unchanged installed AST context builder was run on an existing 90-scene result. Four conditions, three deterministic CPU-only repetitions each:

- 47,000-character setting / 80-scene cap: 55,938 actual characters, 80 scenes, field limits 140/160/160.
- 140,000 / 80: 121,091 characters, 80 scenes, field limits 600/650/650.
- 47,000 / unlimited scene count: 62,803 characters, 90 scenes, field limits 140/160/160.
- 140,000 / unlimited scene count: 135,887 characters, 90 scenes, field limits 600/650/650.

Outputs were identical across deterministic repeats. The character budget is soft, not a hard ceiling. Increasing it also changes per-field clipping, so it does not isolate a pure character-count effect. At the higher budget, comparing 80 against unlimited holds field limits fixed and restores ten omitted scenes. Even the fullest tested condition still clips individual fields.

No model was loaded and no inference quality gain or failure causality was measured here. The source full run's Text JSON stage failed. These contracts do not repair or reinterpret that failure.

## Four-video baseline: interim

At the cutoff, 11 of 12 planned baseline runs had completed: nine retained client responses/execution records and two server-log-only completions. Video D repeat 3 was still running. The initial 54-case short-input Text replay had no result at this snapshot. Before publication, the queue was expanded to 66 cases (54 short-input plus 12 long-context), still waiting for the final long baseline repeat. A separate fixed-frame VLM package was being prepared but had not started. None is counted as completed evidence.

- Video A: three retained responses, 11 scenes each; median client wall time 296.414 s.
- Video B: three retained responses, 7 scenes each; median client wall time 210.327 s.
- Video C: two retained responses, 9 scenes each; third completion is log-only. Do not call its two-response timing summary a complete triplicate.
- Video D: repeat 1 log-only, 90 scenes; repeat 2 retained, 90 scenes, client wall 2544.606 s. Repeat 2 returned HTTP 200 but Text JSON failed after repair.

Video C repeat 3's 178.71 s is server request time, not client wall time. Video D repeat 1's 1769.85 s is likewise server request time. Missing client responses cannot be reconstructed from successful status lines, and those runs are not silently repeated or counted as retained-response quality evidence.

Parsing success does not establish factual accuracy. Review of the three short-input repetitions found recurring UI-state/outcome and name-attribution errors; repeatability alone does not establish truth. Baseline runs were chronological with OCR cache enabled. In the third short-input repeats, Apple OCR fell to 0.073/0.039 s versus seconds earlier. Timings therefore include cache and load/runtime differences; the medians are neither controlled cold-to-cold nor warm-to-warm A/B comparisons. Video A client wall range was 258.650–319.811 s; Video B was 196.397–247.539 s.

## Publication validation and next stage

The companion JSON uses an explicit aggregate-field allowlist. No raw evidence files are included. Privacy review covers only the new publication files, not the repository's pre-existing history. Validate JSON parsing, repeat counts, evidence-class totals, exact changed-file list and whitespace before publication.

Next evidence needed: finish the final baseline repeat; review retained outputs; execute and evaluate the queued Text replay with unchanged inputs; then reconcile remaining parameter gaps. Do not apply a global preset or claim whole-audit completion from these stages.
