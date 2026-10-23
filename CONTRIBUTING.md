# Contributing

- Describe the actual macOS / Apple Silicon hardware and macOS/Python versions used for a test.
- Keep performance numbers tied to the exact source-video duration, scene count, resolution and keyframe count.
- Do not label inference progress as a numeric percent unless the underlying model exposes a measurable completed-work estimate.
- Test both successful completion and cooperative cancellation.
- Confirm that TXT/JSON files actually exist beside the source video; `200 OK` alone is not proof of correct output-directory behavior.
- Never commit videos, model caches, `.venv`, user prompts/settings, or timestamped `.bak` files.
