#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unattended one-factor-at-a-time VCE benchmark. Does not edit local VCE settings."""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, pathlib, re, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, uuid

VIDEO = pathlib.Path.home() / "Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4"
PROJECT = pathlib.Path.home() / "VideoContext-Engine"
BASE_URL = "http://127.0.0.1:7555"
REPO = "https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon.git"
BRANCH = "feat/automated-vce-benchmark"
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
# OCR worker field is detected from the local API's OpenAPI schema; names are not guessed.
def now(): return dt.datetime.now().astimezone().isoformat(timespec="seconds")
def http_json(url, timeout=10):
    req=urllib.request.Request(url, headers={"Accept":"application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode("utf-8"))
def post_form(url, fields, timeout=60):
    boundary="----VCE"+uuid.uuid4().hex
    chunks=[]
    for k,v in fields.items():
        chunks += [f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode(), str(v).encode("utf-8"), b"\r\n"]
    chunks += [f"--{boundary}--\r\n".encode()]
    req=urllib.request.Request(url, data=b"".join(chunks), headers={"Content-Type":f"multipart/form-data; boundary={boundary}", "Accept":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode("utf-8"))
def get_current():
    p=PROJECT/"videocontext_settings.json"
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
def openapi_fields():
    spec=http_json(BASE_URL+"/openapi.json")
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
    return props
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
def git(*args, cwd, check=True):
    p=subprocess.run(["git",*args],cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if check and p.returncode: raise RuntimeError(f"git {' '.join(args)}: {p.stdout[-2000:]}")
    return p.stdout.strip()
def setup_results_repo():
    if shutil.which("gh") is None: raise RuntimeError("Не найден GitHub CLI gh. Один раз выполните gh auth login; без авторизации нельзя коммитить результаты.")
    p=subprocess.run(["gh","auth","status"],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if p.returncode: raise RuntimeError("GitHub CLI не авторизован. Выполните gh auth login один раз до запуска.")
    dest=pathlib.Path.home()/ "VideoContext-Engine-Benchmark-Results"
    if not (dest/".git").is_dir():
        if dest.exists(): raise RuntimeError(f"{dest} существует, но это не git checkout; переименуйте её вручную.")
        subprocess.run(["gh","repo","clone","shushpanchik-art/VideoContext-Engine-Apple-Silicon",str(dest),"--","--branch",BRANCH],check=True)
    else:
        git("fetch","origin",cwd=dest)
        git("checkout",BRANCH,cwd=dest)
        git("pull","--ff-only","origin",BRANCH,cwd=dest)
    return dest
def commit_result(repo_dir, result_path, message):
    git("add",str(result_path.relative_to(repo_dir)),cwd=repo_dir)
    # Empty commits are not needed; each result has a unique file.
    git("commit","-m",message,cwd=repo_dir)
    git("push","origin",BRANCH,cwd=repo_dir)
def run_one(name, overrides, current, props, video, results_dir, index, total, run_id):
    fields={}
    for key in props:
        if key in current: fields[key]=current[key]
    fields.update({"video_file":str(video),"video_url":"","response_format":"json","generate_txt":"false"})
    # Preserve the user's TXT-off preference; never turn TXT on.
    fields["generate_txt"]="false"
    fields.update({k:str(v).lower() if isinstance(v,bool) else str(v) for k,v in overrides.items()})
    # If exposed, request JSON only and keep the user's audio/summary preferences.
    if "generate_summary" in props: fields["generate_summary"]="true"
    start=time.monotonic()
    record={"run_id":run_id,"index":index,"total":total,"name":name,"started_at":now(),"overrides":overrides,"settings_snapshot":{k:fields.get(k) for k in overrides}, "video_path":str(video),"video_size_bytes":video.stat().st_size,"video_sha256":hashlib.sha256(video.read_bytes()).hexdigest() if video.stat().st_size<100_000_000 else None}
    try:
        response=post_form(BASE_URL+"/api/v1/analyze",fields,timeout=60*60*6)
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
    except Exception as exc:
        record["status"]="error"; record["error"]=repr(exc)
    record["duration_seconds"]=round(time.monotonic()-start,2)
    record["finished_at"]=now()
    out=results_dir/f"{index:03d}_{re.sub(r'[^a-zA-Z0-9_.-]+','_',name)}.json"
    out.write_text(json.dumps(record,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return out,record
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--results-dir",default=None)
    args=ap.parse_args()
    if not VIDEO.is_file(): raise RuntimeError(f"Видео не найдено: {VIDEO}")
    if not (PROJECT/"VideoContextEngine_v3.19.py").is_file(): raise RuntimeError(f"Движок не найден: {PROJECT}")
    if not (PROJECT/".venv/bin/python").exists(): print("WARN: .venv не обнаружено; API будет использовать уже запущенный сервер.",file=sys.stderr)
    # Never interfere with an existing run.
    try:
        progress=http_json(BASE_URL+"/api/v1/progress",timeout=5)
        if isinstance(progress,dict) and progress.get("status")=="running":
            raise RuntimeError("VCE сообщает об активном анализе; benchmark не запущен.")
    except urllib.error.HTTPError as e:
        if e.code not in (404,405): raise
    except urllib.error.URLError as e:
        raise RuntimeError(f"VCE API недоступен на {BASE_URL}; запустите VideoContext Engine и повторите команду.") from e
    current=get_current()
    props=openapi_fields()
    worker=pick_worker_field(props)
    plan,ocr_field=make_plan(current,worker,props)
    print(f"Preflight OK. План: {len(plan)} прогонов; файл {VIDEO.name}; OCR toggle={ocr_field}; workers={worker}.")
    if args.dry_run:
        for i,(name,ov) in enumerate(plan,1): print(f"{i:03d}/{len(plan)} {name}: {ov or '(current settings)'}")
        return 0
    repo_dir=setup_results_repo()
    run_id=dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir=pathlib.Path(args.results_dir).expanduser() if args.results_dir else repo_dir/"results"/"vce-benchmark"/run_id
    results_dir.mkdir(parents=True,exist_ok=True)
    manifest={"run_id":run_id,"created_at":now(),"video":VIDEO.name,"video_size_bytes":VIDEO.stat().st_size,"video_sha256":hashlib.sha256(VIDEO.read_bytes()).hexdigest(),"base_settings":current,"plan":[{"name":n,"overrides":o} for n,o in plan],"note":"Public GitHub repository: JSON reports may contain extracted video text. User explicitly requested publishing benchmark results."}
    (results_dir/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    commit_result(repo_dir,results_dir/"manifest.json",f"benchmark: start {run_id} manifest")
    for i,(name,overrides) in enumerate(plan,1):
        print(f"[{i}/{len(plan)}] {name} — {now()}",flush=True)
        out,record=run_one(name,overrides,current,props,VIDEO,results_dir,i,len(plan),run_id)
        # Keep the repo updated after every run so a crash does not lose completed data.
        commit_result(repo_dir,out,f"benchmark: {run_id} run {i:03d} {name}")
        print(f"  -> {record['status']} ({record['duration_seconds']}s); committed {out.name}",flush=True)
    summary={"run_id":run_id,"finished_at":now(),"total_runs":len(plan),"completed":sum(1 for p in results_dir.glob("*.json") if p.name!="manifest.json" and json.loads(p.read_text()).get("status")=="completed"),"errors":sum(1 for p in results_dir.glob("*.json") if p.name!="manifest.json" and json.loads(p.read_text()).get("status")=="error")}
    (results_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    commit_result(repo_dir,results_dir/"summary.json",f"benchmark: {run_id} final summary")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as e:
        print(f"FATAL: {e}",file=sys.stderr); raise SystemExit(2)
