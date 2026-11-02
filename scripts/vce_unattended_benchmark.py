#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unattended one-factor-at-a-time VCE benchmark. Does not edit local VCE settings."""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, mimetypes, pathlib, re, sys, time, urllib.error, urllib.parse, urllib.request, uuid

PROJECT_DEFAULT = pathlib.Path.home() / "VideoContext-Engine"
DEFAULT_API_URL = "http://127.0.0.1:7555"
PARAMS = {
    "vlm_resolution": (128, 2048, [512, 768, 1024, 1280, 1536]),
    "scene_threshold": (0.01, 1.0, [0.15, 0.25, 0.40, 0.60, 0.80]),
    "min_scene_duration": (0.5, 60.0, [0.5, 1.0, 2.0, 4.0, 8.0]),
    "max_scene_duration": (5.0, 300.0, [8.0, 12.0, 20.0, 30.0, 60.0]),
    "keyframes_per_scene": (1, 5, [1, 2, 3, 4, 5]),
    "vlm_max_tokens_scene": (16, 1024, [128, 220, 384, 512, 768]),
    "vlm_max_tokens_summary": (16, 2048, [128, 256, 512, 1024, 1536]),
    "ffmpeg_ocr_interval": (1, 60, [1, 2, 5, 10, 20]),
}
PROMPT_FALLBACKS = {
    "visual_user_prompt": "Анализируй каждую сцену технического видео на русском языке в 1–3 коротких предложениях. Описывай только видимое: интерфейс, объект, действие, важные состояния, читаемые заголовки, числа, формулы, команды, ошибки и результаты. Используй OCR для точного написания видимых названий продуктов, стандартов, файлов и чисел, но учитывай ошибки OCR: при неразборчивом или противоречивом тексте не угадывай. Пересказывай смысл экрана; дословно цитируй только короткие точные подписи и критичные значения. Не вставляй целиком OCR-блоки, временные заголовки [Кадр ...] или длинные перечни элементов интерфейса. Не начинай с «Визуально:» и не смешивай описание с речью диктора. Не делай вывод о результате операции по названию окна или кнопки. Различай наблюдение и предположение; для похожих экранов отмечай конкретные изменения и новые функции. Если показана заставка THE END/КОНЕЦ, опиши только заставку.",
    "summary_user_prompt": "Составь точный и краткий технический конспект на русском по сценам, аудио, OCR и визуальным описаниям. Выдели цель видео, основные этапы, технические детали, нормативы и параметры, решения и фактический итог. Сохраняй точные названия продуктов, стандартов, команд, файлов и числовые значения. Используй OCR для написания чётких видимых подписей; сопоставляй OCR с ASR по контексту и времени, учитывая возможные ошибки OCR. Если подпись однозначно читается и относится к той же сущности, используй её каноническое написание (например, BuildChat вместо фонетического «Белчат»), но не переписывай исходные audio_transcript и ocr_text. Различай сказанное диктором, написанное на экране и непосредственно видимое. Если вывод озвучен, но на кадре только заставка, укажи, что вывод был в аудио, а не на изображении. Название окна не доказывает успешный результат операции. Не выдумывай причинно-следственные связи, нарушения или результаты, не подтверждённые источниками. Не создавай неопределённость, когда источники дополняют друг друга; сомнения отмечай при реальном расхождении или плохой читаемости. Избегай общих фраз и повторов. Заверши краткой сводкой, ключевыми выводами и практическими действиями, если они есть в видео.",
}
PROMPT_LIMITS = {"visual_user_prompt": 1000, "summary_user_prompt": 2000}
PUBLIC_SETTING_KEYS = {
    "vlm_resolution", "scene_threshold", "min_scene_duration", "max_scene_duration",
    "keyframes_per_scene", "vlm_max_tokens_scene", "vlm_max_tokens_summary",
    "ffmpeg_ocr_interval", "ffmpeg_ocr_workers", "enable_ffmpeg_ocr",
    "vlm_model", "whisper_model", "whisper_acceleration", "analysis_mode",
    "skip_audio", "skip_visual", "enable_apple_ocr", "enable_audio_features",
    "generate_summary", "text_llm_enabled", "text_llm_model",
    "text_llm_max_tokens", "generate_json", "generate_txt",
}
# OCR worker field is detected from the local API's OpenAPI schema; names are not guessed.
def now(): return dt.datetime.now().astimezone().isoformat(timespec="seconds")
def sha256_file(path):
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4*1024*1024), b""): h.update(chunk)
    return h.hexdigest()
def http_json(url, timeout=10):
    req=urllib.request.Request(url, headers={"Accept":"application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode("utf-8"))
def post_form(url, fields, timeout=60, file_path=None, file_field="video_file"):
    """Send standard multipart form fields and, when supplied, an actual uploaded video file."""
    boundary="----VCE"+uuid.uuid4().hex
    chunks=[]
    for k,v in fields.items():
        if file_path is not None and k == file_field:
            continue
        chunks += [f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode(), str(v).encode("utf-8"), b"\r\n"]
    if file_path is not None:
        path = pathlib.Path(file_path)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        # Use a stable ASCII upload filename to avoid multipart header issues with emoji filenames.
        chunks += [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{file_field}"; filename="benchmark_video{path.suffix.lower()}"\r\n'.encode("ascii"),
            f"Content-Type: {media_type}\r\n\r\n".encode("ascii"),
            path.read_bytes(),
            b"\r\n",
        ]
    chunks += [f"--{boundary}--\r\n".encode()]
    req=urllib.request.Request(url, data=b"".join(chunks), headers={"Content-Type":f"multipart/form-data; boundary={boundary}", "Accept":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode("utf-8"))
def get_current(project):
    p=project/"videocontext_settings.json"
    if not p.is_file(): raise RuntimeError(f"Не найден файл настроек: {p}")
    data=json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(data,dict): raise RuntimeError("Корень videocontext_settings.json должен быть JSON-объектом")
    return data
def five_values(key, current):
    lo,hi,defaults=PARAMS[key]
    try: c=float(current)
    except (TypeError,ValueError): c=defaults[2]
    # Four values near/far from the current setting, plus a plausible default.
    if key in ("scene_threshold",):
        vals=[max(lo,c*0.85), min(hi,c*1.15), max(lo,c*0.5), min(hi,c*1.8), defaults[2]]
    else:
        vals=[max(lo,c*0.85), min(hi,c*1.15), max(lo,c*0.5), min(hi,c*1.8), defaults[2]]
    if key in ("vlm_resolution","keyframes_per_scene","vlm_max_tokens_scene","vlm_max_tokens_summary"):
        vals=[int(round(v)) for v in vals]
    else: vals=[round(float(v),3) for v in vals]
    # ensure five distinct candidates, within API bounds
    unique=[]
    for v in vals:
        v=max(lo,min(hi,v))
        if v not in unique: unique.append(v)
    for v in defaults:
        if len(unique)>=5: break
        if v not in unique: unique.append(v)
    return unique[:5]
def openapi_fields(base_url):
    spec=http_json(base_url+"/openapi.json")
    op=spec.get("paths",{}).get("/api/v1/analyze",{}).get("post",{})
    body=op.get("requestBody",{}).get("content",{})
    schema={}
    for media in ("multipart/form-data","application/x-www-form-urlencoded"):
        sch=body.get(media,{}).get("schema",{})
        if "$ref" in sch:
            ref=sch["$ref"].split("/")[-1]
            schema=spec.get("components",{}).get("schemas",{}).get(ref,{})
        elif sch: schema=sch
        if schema: break
    props=schema.get("properties",{})
    if not props: raise RuntimeError("OpenAPI не раскрывает поля POST /api/v1/analyze; остановка без изменений.")
    if "video_file" not in props: raise RuntimeError("POST /api/v1/analyze не объявляет video_file; локальный путь не будет отправлен.")
    # The API expects video_file as a multipart file upload, not a local path string.
    return props
def apply_prompt_limits(current, props):
    """Use stable compact prompts when a saved prompt exceeds the API's declared limit.
    This only changes the in-memory benchmark request; it never edits videocontext_settings.json.
    """
    effective = dict(current)
    adjustments = {}
    for key, fallback in PROMPT_FALLBACKS.items():
        value = effective.get(key)
        if not isinstance(value, str):
            continue
        info = props.get(key, {})
        limit = info.get("maxLength")
        if not isinstance(limit, int):
            limit = PROMPT_LIMITS[key]
        if len(value) > limit:
            if len(fallback) > limit:
                raise RuntimeError(f"Встроенный сокращённый промпт {key!r} тоже превышает лимит API: {len(fallback)} > {limit}.")
            effective[key] = fallback
            adjustments[key] = {
                "original_characters": len(value),
                "api_max_characters": limit,
                "effective_characters": len(fallback),
                "method": "replaced in benchmark request with a concise equivalent; local settings file unchanged",
            }
    return effective, adjustments

def pick_worker_field(props):
    for name,info in props.items():
        text=(name+" "+str(info.get("description",""))).lower()
        if ("worker" in text or "parallel" in text) and ("tesseract" in text or "ocr" in text):
            return name
    return None
def make_plan(current, worker_field, props):
    plan=[("baseline",{})]
    for key in PARAMS:
        if key not in props:
            raise RuntimeError(f"Параметр {key!r} отсутствует в OpenAPI локального движка. Не буду подменять его другим параметром.")
        for i,value in enumerate(five_values(key,current.get(key,PARAMS[key][2][2])),1):
            plan.append((f"{key}-{i}",{key:value}))
    if worker_field:
        # five values, with a conservative range for common Tesseract thread pools
        cur=current.get(worker_field,2)
        try: c=int(cur)
        except (TypeError,ValueError): c=2
        vals=[]
        for v in [1,max(1,c-1),c,min(16,c+1),min(32,max(4,c*2))]:
            if v not in vals: vals.append(v)
        for v in [1,2,4,8,16,32]:
            if len(vals)>=5: break
            if v not in vals: vals.append(v)
        if worker_field not in props: raise RuntimeError("Поле числа OCR-воркеров не подтверждено OpenAPI.")
        for i,v in enumerate(vals[:5],1): plan.append((f"{worker_field}-{i}",{worker_field:v}))
    else:
        raise RuntimeError("OpenAPI локального движка не объявляет поле параллельных Tesseract/OCR-воркеров; остановка вместо выдумывания имени.")
    # Explicit baseline comparison with optional FFmpeg/Tesseract OCR toggled.
    ocr_field=None
    for name,info in props.items():
        text=(name+" "+str(info.get("description",""))).lower()
        if "ffmpeg" in text and "ocr" in text and any(w in text for w in ("enable","additional","tesseract","experimental")):
            if info.get("type")=="boolean" or "bool" in str(info.get("type","")).lower():
                ocr_field=name; break
    if not ocr_field:
        raise RuntimeError("OpenAPI не подтверждает boolean-переключатель дополнительного FFmpeg/Tesseract OCR.")
    plan.append((f"{ocr_field}-opposite", {ocr_field: not bool(current.get(ocr_field,False))}))
    return plan,ocr_field
def run_one(name, overrides, current, props, video, results_dir, index, total, run_id, base_url):
    fields={}
    for key in props:
        if key in current: fields[key]=current[key]
    fields.update({"video_url":"","response_format":"json","generate_txt":"false"})
    # Preserve the user's TXT-off preference; never turn TXT on.
    fields["generate_txt"]="false"
    fields.update({k:str(v).lower() if isinstance(v,bool) else str(v) for k,v in overrides.items()})
    # If exposed, request JSON only and keep the user's audio/summary preferences.
    if "generate_summary" in props: fields["generate_summary"]="true"
    start=time.monotonic()
    record={"run_id":run_id,"index":index,"total":total,"name":name,"started_at":now(),"overrides":overrides,"settings_snapshot":{k:fields.get(k) for k in overrides}, "video_filename":video.name,"video_size_bytes":video.stat().st_size,"video_sha256":sha256_file(video) if video.stat().st_size<100_000_000 else None}
    try:
        response=post_form(base_url+"/api/v1/analyze",fields,timeout=60*60*6,file_path=video,file_field="video_file")
        record["api_response"]=response
        # Some local builds return a job ID and expose progress; poll only when identified.
        job_id=response.get("job_id") or response.get("task_id") or response.get("progress_id") if isinstance(response,dict) else None
        if job_id:
            deadline=time.monotonic()+6*60*60
            while time.monotonic()<deadline:
                try: progress=http_json(BASE_URL+"/api/v1/progress/"+urllib.parse.quote(str(job_id),safe=""),timeout=10)
                except Exception as exc:
                    record["progress_poll_error"]=str(exc); time.sleep(10); continue
                record["latest_progress"]=progress
                if progress.get("status") in ("done","completed","error","failed","cancelled"): break
                time.sleep(10)
            else: raise TimeoutError("Истекли 6 часов ожидания завершения задания")
        record["status"]="completed"
    except urllib.error.HTTPError as exc:
        try:
            response_body = exc.read().decode("utf-8", "replace")
        except Exception:
            response_body = ""
        record["status"] = "error"
        record["http_status"] = exc.code
        record["http_response_body"] = response_body[:10000]
        record["error"] = f"HTTP {exc.code} {exc.reason}"
    except Exception as exc:
        record["status"]="error"; record["error"]=repr(exc)
    record["duration_seconds"]=round(time.monotonic()-start,2)
    record["finished_at"]=now()
    out=results_dir/f"{index:03d}_{re.sub(r'[^a-zA-Z0-9_.-]+','_',name)}.json"
    out.write_text(json.dumps(record,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return out,record
def main():
    ap = argparse.ArgumentParser(
        description="Run the VCE one-factor-at-a-time benchmark locally; results are never published automatically."
    )
    ap.add_argument("--video", type=pathlib.Path, required=True, help="Explicit path to the source video.")
    ap.add_argument("--project", type=pathlib.Path, default=PROJECT_DEFAULT, help="Local VideoContext Engine checkout.")
    ap.add_argument("--api-url", default=DEFAULT_API_URL, help="Base URL of the local VCE API.")
    ap.add_argument("--dry-run", action="store_true", help="Validate the plan without analysing the video.")
    ap.add_argument("--results-dir", type=pathlib.Path, help="Local output directory (must be outside the project checkout).")
    args = ap.parse_args()

    video = args.video.expanduser().resolve()
    project = args.project.expanduser().resolve()
    base_url = args.api_url.rstrip("/")
    if not video.is_file():
        raise RuntimeError(f"Видео не найдено: {video}")
    if not (project / "VideoContextEngine_v3.19.py").is_file():
        raise RuntimeError(f"Движок не найден: {project}")
    if not (project / ".venv/bin/python").exists():
        print("WARN: .venv не обнаружено; API будет использовать уже запущенный сервер.", file=sys.stderr)

    try:
        http_json(base_url + "/openapi.json", timeout=5)
    except Exception as exc:
        raise RuntimeError(f"VCE API недоступен на {base_url}; запустите VideoContext Engine и повторите команду.") from exc

    log_path = pathlib.Path.home() / "Library/Logs/VideoContext Engine/server.log"
    if log_path.is_file():
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2_000_000:]
        job_ids = list(dict.fromkeys(re.findall(r"/api/v1/progress/(ui_[A-Za-z0-9_-]+)", tail)))[-20:]
        for job_id in reversed(job_ids):
            try:
                progress = http_json(base_url + "/api/v1/progress/" + urllib.parse.quote(job_id, safe=""), timeout=5)
            except Exception:
                continue
            if progress.get("status") == "running":
                raise RuntimeError(
                    f"VCE уже выполняет задание {job_id} ({progress.get('message', 'без описания')}); benchmark не запущен."
                )

    current = get_current(project)
    props = openapi_fields(base_url)
    current, prompt_adjustments = apply_prompt_limits(current, props)
    for key, adjustment in prompt_adjustments.items():
        print(
            f"WARN: {key} был длиннее лимита API ({adjustment['original_characters']} > "
            f"{adjustment['api_max_characters']}); для прогонов будет использован сокращённый промпт "
            f"({adjustment['effective_characters']} символов). Локальные настройки не меняются.",
            file=sys.stderr,
        )
    worker = pick_worker_field(props)
    plan, ocr_field = make_plan(current, worker, props)
    print(
        f"Preflight OK. План: {len(plan)} прогонов; файл {video.name}; "
        f"OCR toggle={ocr_field}; workers={worker}."
    )
    if args.dry_run:
        for index, (name, overrides) in enumerate(plan, 1):
            print(f"{index:03d}/{len(plan)} {name}: {overrides or '(current settings)'}")
        print("Dry run завершён. Видео не отправлялось; публикации нет.")
        return 0

    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.results_dir:
        results_dir = args.results_dir.expanduser().resolve()
    else:
        results_dir = pathlib.Path.home() / "Downloads" / "VCE_benchmark_runs" / run_id
    try:
        results_dir.relative_to(project)
    except ValueError:
        pass
    else:
        raise RuntimeError("Папка результатов должна находиться вне checkout проекта; используйте ~/Downloads.")
    if results_dir.exists() and any(results_dir.iterdir()):
        raise RuntimeError(f"Папка результатов не пуста: {results_dir}")
    results_dir.mkdir(parents=True, exist_ok=True)

    public_settings = {key: current[key] for key in sorted(PUBLIC_SETTING_KEYS) if key in current}
    manifest = {
        "run_id": run_id,
        "created_at": now(),
        "video_filename": video.name,
        "video_size_bytes": video.stat().st_size,
        "video_sha256": sha256_file(video),
        "base_settings": public_settings,
        "prompt_adjustments": prompt_adjustments,
        "plan": [{"name": name, "overrides": overrides} for name, overrides in plan],
        "storage": "local_only",
        "note": "Per-run JSON files contain full VCE responses and may contain private video-derived text. Do not commit or upload them.",
    }
    (results_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8"
    )
    print("Результаты будут храниться только локально:", results_dir)

    for index, (name, overrides) in enumerate(plan, 1):
        print(f"[{index}/{len(plan)}] {name} — {now()}", flush=True)
        out, record = run_one(
            name, overrides, current, props, video, results_dir, index, len(plan), run_id, base_url
        )
        print(f"  -> {record['status']} ({record['duration_seconds']}s); сохранено {out.name}", flush=True)
        if index == 1 and record["status"] != "completed":
            raise RuntimeError(
                "Первый baseline-прогон завершился ошибкой; benchmark остановлен. "
                "Подробности находятся в локальном JSON результата."
            )

    summary = {
        "run_id": run_id,
        "finished_at": now(),
        "total_runs": len(plan),
        "completed": sum(
            1 for path in results_dir.glob("*.json")
            if path.name != "manifest.json" and json.loads(path.read_text(encoding="utf-8")).get("status") == "completed"
        ),
        "errors": sum(
            1 for path in results_dir.glob("*.json")
            if path.name != "manifest.json" and json.loads(path.read_text(encoding="utf-8")).get("status") == "error"
        ),
        "storage": "local_only",
    }
    (results_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Полные ответы VCE и извлечённый текст остались на этом Mac; загрузки в GitHub не было.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        raise SystemExit(2)
