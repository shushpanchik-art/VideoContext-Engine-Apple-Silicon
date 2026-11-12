# Fixed-frame VLM panel: completed semantic review

Completed 54 calls and review of all outputs: six frozen frames from four private videos, three conditions and three matched-seed repetitions per frame. The conditions are unchanged baseline at resolution 1280, a generic visual-prompt variant at 1280, and unchanged baseline at 1536. This is a targeted stage diagnostic, not a full-video benchmark or a claim about downstream Text quality.

## Method

The installed engine and sampler were retained. All six original full-resolution images were viewed during preparation. Review manually adjudicated 126 rows against 14 frozen criteria using correct, partial, omitted and contradicted labels. It was not keyword scoring. The reviewer could see condition labels, so the assessment was not blinded. Additional errors outside the frozen criteria were recorded separately.

The companion JSON publishes anonymized criterion descriptions and per-condition label counts, not private descriptions or frame text. Negative safety checks remain distinct from positive fact preservation: avoiding one false assertion is not proof that the correct fact or UI state was understood. No pooled headline quality score is calculated.

## Quality findings

Counts below are baseline 1280 / prompt variant 1280 / baseline 1536, with three repetitions per condition for each relevant frame.

- Correct recognition of gray placeholder text as a placeholder: 0/3, 0/3, 0/3. The same state failure persists on a second frame. Transcribing the string does not establish its UI state.
- Correct recognition of an open, unselected menu: 0/3, 0/3, 0/3. One higher-resolution output explicitly asserted a false selection; other outputs omitted the state, which is not a demonstrated correction.
- Complete target table bindings: 0/3, 0/3, 1/3. The higher-resolution condition succeeds once; lists of unbound numbers do not count as complete table recovery.
- Creation confirmation retained: 3/3, 2/3, 3/3. The prompt variant sometimes preserves more detail, but introduces incorrect bindings for other table rows in 2/3 outputs of this frame. Those additional errors are separate from the frozen target criterion.
- Measured/allowed pair retained correctly: 2/3, 2/3, 3/3. No output reverses the pair. Nonintersection is mentioned in 2/3 for each condition, but none explicitly distinguishes it from sufficient clearance. No output asserts adequate clearance; that absence is not full semantic understanding.
- Full conditional exception retained: 0/3, 0/3, 0/3. All nine outputs omit the exception. Object/condition binding remains partial or missing; one prompt-variant output conflates applicability conditions. Avoiding a known unsupported background reading does not establish complete requirement coverage.

Other observed error categories include invented progress-popup text, misreading a checkbox label as an entered title, treating a button as a selected category and unsupported attribution. No condition reliably describes report-preparation state. Private source-specific values and names are intentionally omitted.

## Timing and validity

All 54 responses parsed and matched the expected JSON schema despite the semantic errors.

- Baseline 1280: median 13.697 seconds; range 11.190–18.359.
- Prompt variant 1280: median 15.531 seconds; range 11.813–19.152.
- Baseline 1536: median 16.371 seconds; range 14.073–23.043.

Across 18 matched frame/repetition pairs, the prompt variant has median delta +1.463 seconds and median ratio 1.110 versus baseline; it is faster in 7/18 pairs. Resolution 1536 has median delta +2.250 seconds and median ratio 1.171; it is slower in all 18/18 pairs. These paired medians are not differences or ratios of the overall medians.

These are synchronized describe-scene call wall times, including variable output lengths, not pure image-encoder throughput or full-video duration. Model loading was measured separately at 4.666 seconds; the full session took approximately 861.4 seconds. Matched seeds control assignment, not identical random trajectories under different prompts or resolutions.

## Runtime integrity and limits

Apple M1 Max GPU selection was checked before and after all calls, and an explicit Metal smoke test passed. This verifies device selection, not per-operator placement. Effective sampling remained temperature 0.7, top-p 0.8 and top-k 20; the original application's temperature-zero call argument was not changed. The effective sampler, rather than that argument alone, determines how to interpret the stochastic repetitions.

Forty-three resource snapshots showed AC power, no thermal/performance warnings and no observed swap growth. These sampled observations are not proof that every transient condition was captured. Runtime source and saved-settings hashes remained unchanged.

Only six deliberately difficult frames from four videos and three stochastic repeats were evaluated. This is not a statistically powered superiority study. OCR and ASR inputs were held fixed. It does not establish full-video accuracy, universal resolution optimality or downstream Text improvements.

## Decision and publication boundary

Retain the 1280 baseline as the conservative choice for this tested panel. Higher resolution sometimes preserves small numerical fields, but neither candidate demonstrates a reliable broad quality gain sufficient to justify changing defaults. Follow-up should target UI-state grounding and complete relational facts rather than assume stronger prompt emphasis or resolution is a confirmed fix. No candidate was applied automatically.

This report publishes only aggregate labels, timing, settings, method and limitations. No private descriptions, filenames, frame text, screenshots, local paths or source-specific numerical facts are included. Other completed stages retain their own scope; this does not declare the entire settings audit complete. No merge or branch deletion is part of this publication.
