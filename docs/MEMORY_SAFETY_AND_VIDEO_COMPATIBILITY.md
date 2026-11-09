# Memory safety and video decode recovery

> **Архивная инструкция:** файл описывает исторический патч. Текущий `VideoContextEngine_v3.19.py` уже содержит эту функциональность; патчеры в `patches/archive/` сохранены для истории и не предназначены для запуска.


The VCE runtime may hit a Metal allocation failure or GPU command-buffer timeout while running MLX inference. A timeout followed by `kIOGPUCommandBufferCallbackErrorSubmissionsIgnored` indicates that Metal is no longer accepting work after the prior GPU error; repeatedly submitting the same VLM workload is not a useful recovery strategy. On macOS this can also be aggravated by cached PyTorch MPS allocations left after Whisper inference. Large file size alone does not prove the cause; frame resolution, model allocations, other GPU clients, and unified-memory pressure matter.

The current engine already includes the memory cleanup and media recovery behavior described below. Do not download or run the archived migration patcher. Use the supported runtime and media settings in the VCE UI.

## What it changes

- Adds explicit cleanup of unused PyTorch MPS and MLX allocator caches between model stages.
- If an MLX-VLM call raises a recognized GPU out-of-memory exception, retries the same model/output request with smaller copies of the scene frames (768 px, then 512 px). Original frames used by OCR are not mutated.
- If Metal reports a GPU command-buffer timeout or `SubmissionsIgnored`, unloads the VLM and skips further visual inference for the rest of that video. Whisper, Apple Vision OCR, optional FFmpeg/Tesseract OCR, and report saving continue; affected visual descriptions are explicitly marked as unavailable.
- Changes the saved VLM input cap to 768 px, while preserving up to two keyframes per scene. Apple Vision OCR and optional FFmpeg/Tesseract OCR remain independently configured.
- If OpenCV cannot decode a selected file's first frame, checks the streams with ffprobe and attempts a temporary H.264/MP4 proxy through FFmpeg. The proxy is stored in the system temporary directory and deleted after the request; the original video is never overwritten or removed.
- Preserves model selection, audio processing, prompts, JSON setting, and the current `generate_txt` setting.

The compatibility conversion runs only if OpenCV cannot decode the original file. If ffprobe cannot find a valid video stream, or FFmpeg cannot produce a readable proxy, the engine returns a more specific diagnostic instead of the generic “Fichier vidéo invalide”.

## Restart

Code changes take effect only after the VCE server is restarted. Do not stop or replace a running server during an analysis. After confirming the queue has no active item, stop the server that owns port 7555 and reopen VideoContext Engine. Do not rerun the full installer or the speed-profile patcher just to apply this fix, because the speed-profile patch can restore a higher resolution cap.

## Limitations

Metal OOM can sometimes terminate a process from native callback code before Python can catch it. Lowering the configured image size and releasing cached allocations reduce that risk; the retry provides a second chance where the exception is catchable, but no software retry can guarantee success if other GPU workloads consume the available memory. An automatic fallback to a smaller VLM model is intentionally not performed.
