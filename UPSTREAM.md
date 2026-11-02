# Upstream attribution and base revision

This repository includes a customized copy of the VideoContext Engine v3.19 runtime, not only patch scripts.

- Upstream repository: https://github.com/dolphin-creator/VideoContext-Engine
- Upstream author: `dolphin-creator`
- Reference branch: `main`
- Reference commit used during preparation: `4a42b4a309d9d7d8f7f944c2e210a027940208f3`
- Reference upstream `VideoContextEngine_v3.19.py` blob SHA: `cfa22be84dfbb5f9e6f29269c9906c376e90ef17`

The engine source header identifies VideoContext Engine v3.19, names dolphin-creator as author, and states MIT as its license. The original upstream source has been modified in this repository by applying the published Apple Silicon, UI, memory-safety, OCR, URL-ingestion and reporting changes. See `THIRD_PARTY_LICENSES.md` for upstream license attribution.

This is an independent customization. It is not maintained, endorsed, or reviewed by the upstream author. The upstream project describes v3.19 as a public beta; users should re-run the regression suite when moving to a newer upstream version.

The local patch set and native launcher are separate contributions; see the repository `LICENSE` for their terms.
