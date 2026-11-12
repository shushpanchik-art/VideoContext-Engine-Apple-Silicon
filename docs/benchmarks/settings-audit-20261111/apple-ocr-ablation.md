# Apple Vision OCR prompt ablation: completed review

Eighteen new OCR-off calls were compared with 18 historical matched baseline calls: six frozen frames from four private videos, three matching seeds per frame, resolution 1280. All new results and 42 paired rubric rows were manually reviewed. Keep Apple Vision OCR enabled for this tested panel: removal did not fix UI-state errors and reduced useful numerical retention. This is exploratory evidence, not a universal causal guarantee.

## What changed

Only the conditional Apple Vision OCR prompt block, including its heading, text and hint instruction, was removed. ASR remained byte-identical, along with the base, user and language instructions. Images, seeds, model, resolution, output-token limit (1024) and effective sampler (temperature 0.7, top-p 0.8, top-k 20) were held fixed. No FFmpeg/Tesseract source was present.

This simulates the prompt-level effect of disabling Apple OCR. It does not run a complete video with OCR disabled, measure OCR extraction time or change saved application preferences. The original 18 baseline calls were reused, not repeated contemporaneously. The historical comparator is described in [PR #57](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/57).

## Paired semantic findings

Counts are OCR-on to OCR-off, with three repetitions per condition for each relevant frame.

- Correctly retained measured/allowed numerical pair: 2/3 to 0/3. Nonintersection was quoted in 2/3 historical outputs and none of the new outputs. No new adequate-clearance assertion appeared, but avoiding that assertion is not complete correct understanding.
- Correct placeholder recognition: 0/3 to 0/3. Outputs still treat the placeholder as a formula with OCR absent, so supplied OCR text is not necessary for this error to occur.
- Correct open, unselected menu recognition: 0/3 to 0/3. Both conditions omit the state. Neither condition's lack of an explicit false selection demonstrates a fix.
- Complete target table binding: 0/3 to 0/3. Two OCR-off repeats introduce definite incorrect group/row bindings. A remaining ambiguous attribution is not counted as a definite contradiction. These failures cannot be attributed to OCR alone.
- Creation confirmation retention: 3/3 to 3/3. Some generated-sentence content remains partially retained in 2/3 for each condition, without consistently correct state recognition.
- Full conditional exception coverage: 0/3 to 0/3. One OCR-off output moves a value to another object type; others omit the numerical requirements. A known false background OCR reading is not propagated by either condition, so its mere presence in raw OCR is not evidence that it harmed the baseline answer.

Other categories include persistent invented popup text, unsupported application identity and mislabeling source content as an entered field. Private values, descriptions and identifiers are omitted from publication. Criterion-level counts remain separate; negative safety checks are not pooled with positive retention into a single quality score.

## Validity and descriptive timing

All 18 new outputs parse and pass schema checks, as do the 18 historical outputs. This does not establish semantic quality.

- Historical OCR-on call median: 13.697 seconds, range 11.190–18.359; mean 14.394.
- New OCR-off call median: 13.892 seconds, range 10.929–17.032; mean 13.705.
- Matched off-minus-on median delta: −0.690 seconds; median paired ratio: 0.948. OCR-off is faster in 11/18 pairs.
- New model loading: 4.887 seconds, measured separately. New session duration: approximately 256.96 seconds.

The higher marginal OCR-off median and negative median paired delta are not inconsistent: the latter is computed per matched frame/seed, rather than by subtracting overall medians. Different generated lengths affect call duration. The historical comparison also has time, system-load, cache, model-load and order confounds. Matching seeds does not guarantee identical token trajectories after shortening the prompt.

These are VLM replay-call times. OCR extraction is excluded, so no end-to-end video speedup or OCR-processing-time claim follows from them. The sample is small and nonrandomized; no statistical superiority claim is made.

## Integrity and limitations

Review was manual against frozen images and criteria, with condition labels visible; it was not blinded or keyword-scored. Exact output evidence remains private. GPU selection was checked before and after all new calls, which is device-selection evidence rather than per-operator profiling. Thirteen resource snapshots showed AC power and no thermal/performance warnings.

New swap and system-load measurements were not collected. Do not carry the earlier session's swap observations forward or claim unchanged load for this session. Source and saved-settings hashes remained unchanged. Six difficult frames, four videos and three repeats do not establish coverage of other content, languages or full-video downstream quality.

## Decision

Retain Apple Vision OCR enabled with baseline resolution 1280. Disabling it is not a demonstrated UI-state fix and loses useful numeric detail on this panel. Keep further evaluation focused on state and relational grounding. No new preset is applied, no whole-audit completion is claimed, and no merge or branch deletion is part of this publication.
