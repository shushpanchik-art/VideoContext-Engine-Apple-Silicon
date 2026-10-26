#!/usr/bin/env python3
"""Run one video through local VCE using several setting profiles; compare outputs side by side."""
from __future__ import annotations

import argparse
import html
import http.client
import json
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

FIELDS = {
    "analysis_mode", "vlm_model", "whisper_model", "vlm_resolution",
    "scene_threshold", "min_scene_duration", "max_scene_duration",
    "keyframes_per_scene", "vlm_max_tokens_scene", "vlm_max_tokens_summary",
    "enable_apple_ocr", "enable_ffmpeg_ocr", "ffmpeg_ocr_interval", "ffmpeg_ocr_workers",
    "whisper_acceleration", "enable_audio_features", "generate_summary",
    "generate_txt", "generate_json", "word_timestamps", "visual_user_prompt",
    "summary_user_prompt", "text_llm_enabled", "text_llm_model", "text_llm_max_tokens",
    "skip_audio", "skip_visual",
}

PROMPT_LIMITS = {
    "visual_user_prompt": 1000,
    "summary_user_prompt": 2000,
}
PROMPT_LABELS = {
    "visual_user_prompt": "Визуальный промпт",
    "summary_user_prompt": "Промпт резюме",
}


def validate_prompt_override(field, value, profile_name=None):
    label = PROMPT_LABELS[field]
    location = f" в профиле {profile_name!r}" if profile_name else ""
    if not isinstance(value, str):
        raise ValueError(f"{label}{location} должен быть строкой.")
    limit = PROMPT_LIMITS[field]
    if len(value) > limit:
        raise ValueError(
            f"{label}{location} превышает лимит API: {len(value)} символов; максимум {limit}."
        )


def value_for_form(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def slugify(value):
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._").lower()[:70] or "profile"


def load_profiles(path):
    document = json.loads(path.expanduser().read_text(encoding="utf-8-sig"))
    profiles = document.get("profiles") if isinstance(document, dict) else None
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("JSON-профиль должен содержать непустой массив profiles.")
    out, names, slugs = [], set(), set()
    for index, item in enumerate(profiles, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Профиль #{index} должен быть объектом.")
        name, overrides = item.get("name"), item.get("settings", {})
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"У профиля #{index} нет непустого поля name.")
        name = name.strip()
        if name in names:
            raise ValueError(f"Повторяется имя профиля: {name}")
        if not isinstance(overrides, dict):
            raise ValueError(f"settings профиля {name!r} должен быть объектом.")
        unknown = sorted(set(overrides) - FIELDS)
        if unknown:
            raise ValueError(f"Неизвестные настройки профиля {name!r}: {', '.join(unknown)}")
        for field in PROMPT_LIMITS:
            if field in overrides:
                validate_prompt_override(field, overrides[field], profile_name=name)
        slug = slugify(name)
        if slug in slugs:
            raise ValueError(f"Имена профилей дают одинаковое имя папки: {slug}")
        out.append({"name": name, "slug": slug, "settings": overrides})
        names.add(name)
        slugs.add(slug)
    return out


def read_object(path):
    value = json.loads(path.expanduser().read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Ожидался JSON-объект в {path}")
    return value


def get_server_settings(base_url, timeout):
    url = base_url.rstrip("/") + "/api/v1/ui-settings"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=min(timeout, 20)) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Не удалось получить настройки с {url}: {exc}. Передай --base-settings "
            "путь к videocontext_settings.json."
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(f"Сервер вернул неожиданный формат настроек: {url}")
    return value


def effective_settings(base, overrides):
    # Validate only prompts explicitly authored in this profile. Saved prompts can
    # be longer than the analyze endpoint's Form limits, so do not resend them:
    # omitting these fields makes FastAPI use the engine's persisted defaults.
    for field in PROMPT_LIMITS:
        if field in overrides:
            validate_prompt_override(field, overrides[field])

    merged = {key: value for key, value in base.items() if key in FIELDS}
    merged.update(overrides)
    form = {}
    for key, value in merged.items():
        if key not in FIELDS or value is None:
            continue
        if key in PROMPT_LIMITS and key not in overrides:
            continue
        form[key] = value_for_form(value)
    mode = form.get("analysis_mode", "full").strip().lower()
    if mode not in {"full", "whisper_ocr", "ocr_only"}:
        raise ValueError(f"Недопустимый analysis_mode: {mode!r}")
    form.update({
        "analysis_mode": mode,
        "skip_audio": "true" if mode == "ocr_only" else "false",
        "skip_visual": "true" if mode != "full" else "false",
        "response_format": "json",
        # The runner saves the response itself; avoid VCE overwriting context files in its project folder.
        "generate_txt": "false",
        "generate_json": "false",
    })
    return form


def send_chunk(connection, data):
    if data:
        connection.send(f"{len(data):X}\r\n".encode("ascii"))
        connection.send(data)
        connection.send(b"\r\n")


def post_video(base_url, video_path, fields, timeout):
    """Stream the multipart file upload, keeping large videos out of RAM."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Некорректный адрес API: {base_url!r}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.scheme == "https":
        connection = http.client.HTTPSConnection(parsed.hostname, port, timeout=timeout)
    else:
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
    target = parsed.path.rstrip("/") + "/api/v1/analyze"
    boundary = "----VCECompare" + uuid.uuid4().hex
    mime = mimetypes.guess_type(video_path.name)[0] or "application/octet-stream"
    filename = video_path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    started = time.perf_counter()
    try:
        connection.putrequest("POST", target)
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Transfer-Encoding", "chunked")
        connection.putheader("Connection", "close")
        connection.endheaders()
        for name, value in fields.items():
            send_chunk(connection, (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            ).encode("utf-8"))
            send_chunk(connection, value.encode("utf-8"))
            send_chunk(connection, b"\r\n")
        send_chunk(connection, (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"video_file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n"
        ).encode("utf-8"))
        with video_path.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                send_chunk(connection, chunk)
        send_chunk(connection, b"\r\n")
        send_chunk(connection, f"--{boundary}--\r\n".encode("ascii"))
        connection.send(b"0\r\n\r\n")
        response = connection.getresponse()
        raw = response.read()
        elapsed = time.perf_counter() - started
        text = raw.decode("utf-8", errors="replace")
        if not 200 <= response.status < 300:
            try:
                detail = json.loads(text).get("detail", text)
            except (json.JSONDecodeError, AttributeError):
                detail = text
            raise RuntimeError(f"HTTP {response.status}: {str(detail)[:3000]}")
        result = json.loads(text)
        if not isinstance(result, dict):
            raise RuntimeError("VCE вернул JSON, но корень результата не является объектом.")
        return result, response.status, elapsed
    finally:
        connection.close()


def resolve_video_path(requested_path: Path, search_root: Path | None = None) -> tuple[Path, str | None]:
    """Resolve a missing video path safely; never guess between different video files."""
    requested = requested_path.expanduser()
    if requested.is_file():
        return requested.resolve(), None

    root = (search_root or (Path.home() / "Downloads")).expanduser()
    suffixes = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts"}
    exact_matches = []
    recent_videos = []
    if root.is_dir():
        try:
            for candidate in root.rglob("*"):
                try:
                    if not candidate.is_file() or candidate.suffix.casefold() not in suffixes:
                        continue
                    resolved = candidate.resolve()
                    if candidate.name.casefold() == requested.name.casefold():
                        exact_matches.append(resolved)
                    recent_videos.append((candidate.stat().st_mtime, resolved))
                except OSError:
                    continue
        except OSError:
            pass

    exact_matches = sorted(set(exact_matches), key=lambda item: str(item).casefold())
    if len(exact_matches) == 1:
        resolved = exact_matches[0]
        return resolved, (
            f"Внимание: исходный путь не найден; автоматически найден файл с тем же именем: {resolved}"
        )
    if len(exact_matches) > 1:
        options = "\n".join(f"  - {candidate}" for candidate in exact_matches[:10])
        raise ValueError(
            f"Видео не найдено по указанному пути: {requested}\n"
            f"В Downloads найдено несколько файлов с именем {requested.name!r}; выбери нужный явно:\n{options}"
        )

    recent_videos.sort(key=lambda item: item[0], reverse=True)
    suggestions = []
    seen = set()
    for _mtime, candidate in recent_videos:
        if candidate in seen:
            continue
        seen.add(candidate)
        suggestions.append(candidate)
        if len(suggestions) >= 15:
            break
    if suggestions:
        options = "\n".join(f"  - {candidate}" for candidate in suggestions)
        hint = f"\nНедавно изменённые видео в {root}:\n{options}"
    else:
        hint = f"\nВ {root} не найдены видеофайлы поддерживаемых форматов."
    raise ValueError(
        f"Видео не найдено: {requested}\n"
        "Проверь имя и расположение файла. VCE Compare не выбрал другое видео автоматически."
        + hint
    )


def format_time(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        milliseconds = int(round(value * 1000))
        hours, rest = divmod(milliseconds, 3600000)
        minutes, rest = divmod(rest, 60000)
        seconds, millis = divmod(rest, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return "" if value is None else str(value)


def _stage_times(payload):
    """Return a stable set of per-stage durations from the VCE result."""
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    timings = meta.get("timings") if isinstance(meta.get("timings"), dict) else {}
    stages = []
    for key, label in (
        ("whisper", "Whisper"),
        ("vlm", "Qwen3-VL"),
        ("apple_ocr", "Apple OCR"),
        ("ffmpeg_ocr", "FFmpeg/Tesseract OCR"),
    ):
        stage = timings.get(key)
        if not isinstance(stage, dict):
            continue
        if key == "ffmpeg_ocr" and not stage.get("requested", False):
            continue
        if key == "apple_ocr" and stage.get("enabled") is False:
            continue
        if key == "vlm" and (meta.get("skipped", {}).get("visual") is True
                             or meta.get("params", {}).get("run_vlm") is False):
            continue
        seconds = stage.get("inference_time")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
            stages.append((label, float(seconds)))
    text_llm = payload.get("text_llm")
    if isinstance(text_llm, dict):
        seconds = text_llm.get("inference_time")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
            stages.append(("Qwen3.5 text", float(seconds)))
    return stages


def make_report(name, payload, elapsed):
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    timings = meta.get("timings") if isinstance(meta.get("timings"), dict) else {}
    enhanced = payload.get("enhanced_analysis") if isinstance(payload.get("enhanced_analysis"), dict) else {}
    text_llm = payload.get("text_llm") if isinstance(payload.get("text_llm"), dict) else {}
    stages = _stage_times(payload)
    lines = [
        f"Профиль: {name}",
        f"Видео: {meta.get('source', '')}",
        f"Длительность видео, с: {meta.get('duration', '')}",
        f"Время VCE, с: {meta.get('process_time', timings.get('total_process_time', ''))}",
        f"Полное время HTTP-запроса, с: {elapsed:.2f}",
        f"Количество сцен: {meta.get('scene_count', '')}",
    ]
    if stages:
        lines.extend(["", "ВРЕМЯ ПО ЭТАПАМ"])
        lines.extend(f"- {label}: {seconds:.2f} с" for label, seconds in stages)
    if text_llm:
        lines.extend([
            "",
            "ТЕКСТОВАЯ ПОСТОБРАБОТКА",
            f"- Статус: {text_llm.get('status', 'не указан')}",
            f"- Модель: {text_llm.get('model', 'не указана')}",
        ])
        if text_llm.get("error"):
            lines.append(f"- Ошибка: {text_llm['error']}")

    executive = enhanced.get("executive_summary")
    if isinstance(executive, str) and executive.strip():
        lines += ["", "СТРУКТУРИРОВАННЫЙ ИТОГ (Qwen3.5)", executive.strip()]
    global_summary = meta.get("global_summary")
    if isinstance(global_summary, str) and global_summary.strip():
        lines += ["", "ГЛОБАЛЬНАЯ СВОДКА", global_summary.strip()]

    topics = enhanced.get("topics")
    if isinstance(topics, list) and topics:
        lines += ["", "ТЕМЫ"]
        lines.extend(f"- {item}" for item in topics if isinstance(item, str) and item.strip())

    key_points = enhanced.get("key_points")
    if isinstance(key_points, list) and key_points:
        lines += ["", "КЛЮЧЕВЫЕ МОМЕНТЫ"]
        for point in key_points:
            if not isinstance(point, dict):
                continue
            scene_id = point.get("scene_id", "?")
            timestamp = point.get("time", "")
            text = point.get("text", "")
            evidence = point.get("evidence") or []
            lines.append(f"- Сцена {scene_id} {f'({timestamp}) ' if timestamp else ''}{text}")
            if evidence:
                lines.append("  Источники: " + ", ".join(str(value) for value in evidence))

    entities = enhanced.get("entities")
    if isinstance(entities, list) and entities:
        lines += ["", "СУЩНОСТИ"]
        for entity in entities:
            if isinstance(entity, dict) and entity.get("name"):
                kind = entity.get("type", "")
                scenes = entity.get("scene_ids") or []
                suffix = f"; сцены: {', '.join(map(str, scenes))}" if scenes else ""
                lines.append(f"- {entity['name']}" + (f" ({kind})" if kind else "") + suffix)

    uncertainties = enhanced.get("uncertainties")
    if isinstance(uncertainties, list) and uncertainties:
        lines += ["", "НЕОПРЕДЕЛЁННОСТИ"]
        lines.extend(f"- {value}" for value in uncertainties if isinstance(value, str) and value.strip())

    scene_enrichment = enhanced.get("scene_enrichment")
    if isinstance(scene_enrichment, list) and scene_enrichment:
        lines += ["", "ОПИСАНИЯ СЦЕН ПОСЛЕ ПОСТОБРАБОТКИ"]
        for scene in scene_enrichment:
            if not isinstance(scene, dict):
                continue
            scene_id = scene.get("scene_id", "?")
            topic = scene.get("topic", "")
            description = scene.get("improved_description", "")
            lines.append(f"- Сцена {scene_id}" + (f" — {topic}" if topic else ""))
            if description:
                lines.append(f"  {description}")
            for point in scene.get("key_points") or []:
                if isinstance(point, str) and point.strip():
                    lines.append(f"  • {point}")

    segments = payload.get("segments", [])
    if isinstance(segments, list):
        lines += ["", "ИСХОДНЫЕ ДАННЫЕ ПО СЦЕНАМ"]
        for i, segment in enumerate(segments, 1):
            if not isinstance(segment, dict):
                continue
            start = segment.get("start", segment.get("start_time", segment.get("start_seconds")))
            end = segment.get("end", segment.get("end_time", segment.get("end_seconds")))
            stamp = format_time(start)
            if end is not None:
                stamp += "–" + format_time(end)
            lines.append("[" + stamp + "]" if stamp else f"[Сцена {i}]")
            for key, label in (("audio_transcript", "Речь"), ("visual_description", "Визуально"),
                               ("ocr_text", "Apple OCR"), ("ffmpeg_ocr_text", "FFmpeg OCR"),
                               ("detected_text", "OCR")):
                value = segment.get(key)
                if isinstance(value, str) and value.strip():
                    lines.append(f"{label}: {value.strip()}")
            segment_llm = segment.get("text_llm")
            if isinstance(segment_llm, dict):
                for key, label in (("topic", "Тема"), ("improved_description", "Описание"),
                                   ("key_points", "Ключевые моменты")):
                    value = segment_llm.get(key)
                    if isinstance(value, list):
                        value = "; ".join(str(v) for v in value)
                    if isinstance(value, str) and value.strip():
                        lines.append(f"Постобработка — {label}: {value.strip()}")
            if isinstance(segment.get("audio_features"), dict):
                lines.append("Аудиопризнаки: " + json.dumps(segment["audio_features"], ensure_ascii=False))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_comparison(out_dir, video_path, results):
    created = datetime.now().astimezone().isoformat(timespec="seconds")
    table = [
        "# Сравнение запусков VideoContext Engine", "",
        f"- Исходное видео: {video_path}",
        f"- Создано: {created}",
        "- Эталонная транскрипция не требуется; WER/CER не вычисляются.", "",
        "| Профиль | Статус | VCE, с | HTTP, с | Whisper, с | VLM, с | Apple OCR, с | FFmpeg OCR, с | Qwen3.5, с | Сцен | Папка |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    cards = []
    for item in results:
        payload = item.get("payload", {})
        meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
        timings = meta.get("timings", {}) if isinstance(meta.get("timings"), dict) else {}
        api_time = meta.get("process_time", timings.get("total_process_time", "—"))
        elapsed = item.get("elapsed_seconds", "—")
        scenes = meta.get("scene_count", "—")
        stage_values = {label: seconds for label, seconds in _stage_times(payload)} if isinstance(payload, dict) else {}
        whisper = f"{stage_values['Whisper']:.1f}" if "Whisper" in stage_values else "—"
        vlm = f"{stage_values['Qwen3-VL']:.1f}" if "Qwen3-VL" in stage_values else "—"
        apple_ocr = f"{stage_values['Apple OCR']:.1f}" if "Apple OCR" in stage_values else "—"
        ffmpeg_ocr = f"{stage_values['FFmpeg/Tesseract OCR']:.1f}" if "FFmpeg/Tesseract OCR" in stage_values else "—"
        text_llm_time = f"{stage_values['Qwen3.5 text']:.1f}" if "Qwen3.5 text" in stage_values else "—"
        table.append(f"| {item['name']} | {item['status']} | {api_time} | {elapsed} | {whisper} | {vlm} | {apple_ocr} | {ffmpeg_ocr} | {text_llm_time} | {scenes} | runs/{item['slug']} |")
        name = html.escape(item["name"])
        setting_text = html.escape(json.dumps(item.get("effective_settings", {}), ensure_ascii=False, indent=2))
        if item["status"] == "ok":
            enhanced = payload.get("enhanced_analysis", {}) if isinstance(payload, dict) else {}
            summary = enhanced.get("executive_summary") or meta.get("global_summary", "")
            report = html.escape(item.get("report_text", ""))
            summary_html = "<h3>Структурированный итог</h3><p>" + html.escape(str(summary)) + "</p>" if summary else ""
            stages = _stage_times(payload) if isinstance(payload, dict) else []
            stages_html = "<h3>Время по этапам</h3><ul>" + "".join(
                "<li>" + html.escape(label) + ": " + f"{seconds:.2f}" + " с</li>" for label, seconds in stages
            ) + "</ul>" if stages else ""
            cards.append(
                '<section class="card"><h2>' + name + '</h2><p class="ok">Готово</p>'
                + '<div class="stats"><div><small>Время VCE</small><strong>' + html.escape(str(api_time))
                + ' с</strong></div><div><small>Полное время запроса</small><strong>' + html.escape(str(elapsed))
                + ' с</strong></div><div><small>Сцен</small><strong>' + html.escape(str(scenes))
                + '</strong></div></div>' + stages_html + summary_html + '<h3>Подробный отчёт</h3><pre>' + report
                + '</pre><details><summary>Настройки запуска</summary><pre>' + setting_text
                + '</pre></details><p><a href="runs/' + html.escape(item["slug"])
                + '/result.json">JSON</a> · <a href="runs/' + html.escape(item["slug"])
                + '/report.txt">TXT</a></p></section>'
            )
        else:
            error = html.escape(item.get("error", "Неизвестная ошибка"))
            cards.append(
                '<section class="card"><h2>' + name + '</h2><p class="fail">Ошибка</p><pre>'
                + error + '</pre><details><summary>Настройки запуска</summary><pre>'
                + setting_text + '</pre></details></section>'
            )
        table += ["", f"## {item['name']}", ""]
        if item["status"] == "ok":
            table += [
                f"- Результат: runs/{item['slug']}/report.txt",
                f"- Полный JSON: runs/{item['slug']}/result.json",
                f"- Настройки: runs/{item['slug']}/settings.json", "",
            ]
        else:
            table += [f"- Ошибка: {item.get('error', 'неизвестная ошибка')}", ""]
    (out_dir / "comparison.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Сравнение запусков VCE</title>
<style>
body{font:15px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;margin:0;padding:24px;background:Canvas;color:CanvasText}
header{max-width:1700px;margin:0 auto 20px}h1{font-size:27px}.sub{color:GrayText;overflow-wrap:anywhere}
.grid{max-width:1800px;margin:auto;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,500px),1fr));gap:16px;align-items:start}
.card{border:1px solid #8886;border-radius:14px;padding:18px;background:rgba(127,127,127,.06);min-width:0}h2{font-size:20px;margin:0 0 4px}
h3{font-size:15px;margin:18px 0 8px}.ok{color:#248a45;font-weight:600}.fail{color:#c53535;font-weight:600}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.stats>div{border:1px solid #8886;border-radius:9px;padding:9px}
small{display:block;color:GrayText}strong{display:block;font-size:18px;overflow-wrap:anywhere}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,Menlo,monospace;background:rgba(127,127,127,.1);border-radius:8px;padding:12px}
details{margin-top:12px}summary{cursor:pointer;font-weight:600}
</style></head><body><header><h1>Сравнение запусков VideoContext Engine</h1>
<p class="sub">Один исходный файл: """ + html.escape(str(video_path)) + """</p>
<p class="sub">Создано: """ + html.escape(created) + """ · Профилей: """ + str(len(results)) + """ · Запуски последовательные.</p>
<p>Сравнивай текст, общий итог, состав сцен и время. Отдельная эталонная транскрипция не требуется.</p>
</header><main class="grid">""" + "".join(cards) + "</main></body></html>"
    (out_dir / "comparison.html").write_text(page, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True, help="Один и тот же исходный файл для всех профилей.")
    parser.add_argument("--profiles", type=Path, required=True, help="JSON с массивом profiles и изменениями настроек.")
    parser.add_argument("--out-dir", type=Path, required=True, help="Новая или пустая папка для результатов.")
    parser.add_argument("--base-url", default="http://127.0.0.1:7555", help="Адрес локального VCE.")
    parser.add_argument("--base-settings", type=Path, help="Путь к videocontext_settings.json; по умолчанию настройки читаются из API.")
    parser.add_argument("--timeout", type=float, default=7200, help="Таймаут каждого прогона в секундах.")
    args = parser.parse_args(argv)
    try:
        video, video_notice = resolve_video_path(args.video)
        if video_notice:
            print(video_notice, file=sys.stderr, flush=True)
        profiles_path = args.profiles.expanduser().resolve()
        out_dir = args.out_dir.expanduser().resolve()
        if args.timeout <= 0:
            raise ValueError("--timeout должен быть положительным.")
        if out_dir.exists() and any(out_dir.iterdir()):
            raise ValueError(f"Папка результатов не пуста: {out_dir}. Выбери новую папку, чтобы ничего не перезаписать.")
        profiles = load_profiles(profiles_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        if args.base_settings:
            base = read_object(args.base_settings)
            settings_source = str(args.base_settings.expanduser().resolve())
        else:
            base = get_server_settings(args.base_url, args.timeout)
            settings_source = args.base_url.rstrip("/") + "/api/v1/ui-settings"
        results = []
        for index, profile in enumerate(profiles, 1):
            run_dir = out_dir / "runs" / profile["slug"]
            run_dir.mkdir(parents=True, exist_ok=True)
            form = effective_settings(base, profile["settings"])
            record = {
                "name": profile["name"], "slug": profile["slug"],
                "status": "failed", "effective_settings": form, "elapsed_seconds": None,
            }
            write_json(run_dir / "settings.json", {
                "profile": profile["name"], "source_settings": settings_source,
                "submitted_form_fields": form, "profile_overrides": profile["settings"], "video": str(video),
            })
            print(f"[{index}/{len(profiles)}] {profile['name']}: {video.name}", flush=True)
            try:
                payload, status, elapsed = post_video(args.base_url, video, form, args.timeout)
                report = make_report(profile["name"], payload, elapsed)
                write_json(run_dir / "result.json", payload)
                (run_dir / "report.txt").write_text(report, encoding="utf-8")
                record.update({"status": "ok", "payload": payload, "http_status": status,
                               "elapsed_seconds": round(elapsed, 3), "report_text": report})
                write_json(run_dir / "run.json", {
                    "profile": profile["name"], "status": "ok", "http_status": status,
                    "elapsed_seconds": round(elapsed, 3),
                    "api_process_seconds": payload.get("meta", {}).get("process_time") if isinstance(payload.get("meta"), dict) else None,
                    "video_duration_seconds": payload.get("meta", {}).get("duration") if isinstance(payload.get("meta"), dict) else None,
                    "stage_timings": {label: seconds for label, seconds in _stage_times(payload)},
                    "text_llm": payload.get("text_llm") if isinstance(payload.get("text_llm"), dict) else None,
                    "settings": form,
                })
                print(f"    готово: время запроса {elapsed:.1f} с", flush=True)
            except Exception as exc:
                record["error"] = str(exc)
                (run_dir / "error.txt").write_text(str(exc) + "\n", encoding="utf-8")
                write_json(run_dir / "run.json", {"profile": profile["name"], "status": "failed", "error": str(exc), "settings": form})
                print(f"    ошибка: {exc}", file=sys.stderr, flush=True)
            results.append(record)
            write_comparison(out_dir, video, results)
        good = sum(item["status"] == "ok" for item in results)
        print(f"\nГотово: {good} из {len(results)} запусков успешно.")
        print(f"Сравнение в браузере: {out_dir / 'comparison.html'}")
        print(f"Markdown-сводка: {out_dir / 'comparison.md'}")
        return 0 if good == len(results) else 1
    except KeyboardInterrupt:
        print("\nОстановлено. Уже завершённые результаты сохранены.", file=sys.stderr)
        return 130
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"VCE COMPARE ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
