#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local A-E benchmark for VideoContext Engine Text LLM. Video and raw scene data stay local."""
from __future__ import annotations
import argparse, base64, copy, csv, datetime as dt, hashlib, importlib, importlib.util, inspect, json, os, platform, re, statistics, subprocess, sys, tempfile, time, urllib.error, urllib.request, uuid
from pathlib import Path

REPO = "shushpanchik-art/VideoContext-Engine-Apple-Silicon"
BRANCH = "main"
SOURCE_SHA = "1eb9295a65fb90da980d9104c80d65fbcbeec6c8"
SOURCE_URL = f"https://raw.githubusercontent.com/{REPO}/{SOURCE_SHA}/artifacts/source-snapshots/VideoContextEngine_v3.19.py"
VIDEO_DEFAULT = Path("/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4")
MODEL9 = "mlx-community/Qwen3.5-9B-MLX-4bit"
MODEL4 = "mlx-community/Qwen3.5-4B-MLX-4bit"
DRAFTER9 = "mlx-community/Qwen3.5-9B-MTP-4bit"
NOISE = 5.0
SCRIPT_VERSION = "2026.10.27.7-full-output-4b-benchmark"

def validate_text_llm_payload(payload, segments, state=None):
    """Generic schema/integrity check. Deliberately does not rewrite generated content."""
    flags = []
    if not isinstance(payload, dict):
        flags.append("output_not_json_object")
        if state is not None:
            state.quality_guard_flags += len(flags)
        print("⚠️ [Benchmark integrity guard] Output is not a JSON object; content not rewritten.")
        return payload
    expected_ids = set()
    if isinstance(segments, list):
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                flags.append(f"source_scene[{index}]_not_object")
                continue
            try:
                expected_ids.add(int(segment.get("scene_id", index + 1)))
            except (TypeError, ValueError):
                flags.append(f"source_scene[{index}]_invalid_id")

    for field, require_all in (("key_points", False), ("scene_enrichment", True)):
        records = payload.get(field)
        if not isinstance(records, list):
            flags.append(f"{field}_not_array")
            continue
        ids = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                flags.append(f"{field}[{index}]_not_object")
                continue
            try:
                ids.append(int(record.get("scene_id")))
            except (TypeError, ValueError):
                flags.append(f"{field}[{index}]_invalid_scene_id")
        if len(ids) != len(set(ids)):
            flags.append(f"{field}_duplicate_scene_ids")
        unexpected = sorted(set(ids) - expected_ids)
        if unexpected:
            flags.append(f"{field}_unexpected_scene_ids:{unexpected}")
        if require_all:
            missing = sorted(expected_ids - set(ids))
            if missing:
                flags.append(f"{field}_missing_scene_ids:{missing}")

    summary = payload.get("executive_summary")
    if not isinstance(summary, str) or not summary.strip():
        flags.append("executive_summary_empty_or_missing")
    for field in ("topics", "entities", "uncertainties"):
        if not isinstance(payload.get(field), list):
            flags.append(f"{field}_not_array")

    texts = [summary] if isinstance(summary, str) else []
    for field in ("topics", "entities", "uncertainties"):
        values = payload.get(field)
        if isinstance(values, list):
            texts.extend(item if isinstance(item, str) else str(item) for item in values)
    for field in ("key_points", "scene_enrichment"):
        values = payload.get(field)
        if isinstance(values, list):
            for item in values:
                if not isinstance(item, dict):
                    continue
                for key in ("text", "description", "topic", "improved_description"):
                    if isinstance(item.get(key), str):
                        texts.append(item[key])
                child = item.get("key_points")
                if isinstance(child, list):
                    texts.extend(str(value) for value in child)
    for index, value in enumerate(texts):
        if "\ufffd" in value:
            flags.append(f"text[{index}]_unicode_replacement_character")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            flags.append(f"text[{index}]_control_character")

    if state is not None:
        state.quality_guard_flags += len(flags)
    if flags:
        print(f"⚠️ [Benchmark integrity guard] {len(flags)} generic integrity flags; generated content was not rewritten.")
    return payload



def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def parse_primary_model_output(primary_raw: str | None) -> dict | None:
    """Parse the primary model JSON before VCE finalization, excluding private think blocks."""
    if not isinstance(primary_raw, str) or not primary_raw.strip():
        return None
    raw = re.sub(r"<think>.*?</think>", "", primary_raw, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

def build_output_record(iteration, output, raw_model_output, metrics):
    """Keep model-authored JSON distinct from the finalized VCE payload."""
    return {
        "iteration": iteration,
        "output": output,
        "raw_model_output": raw_model_output,
        "output_provenance": {
            "output": "vce_finalized_payload_after_postprocess",
            "raw_model_output": "parsed_primary_model_json_before_vce_finalize",
            "prompt_rewrite_applied": metrics.get("prompt_rewrite_ok"),
            "raw_scene_enrichment_count": metrics.get("generated_scene_enrichment_count"),
            "raw_scene_enrichment_empty": metrics.get("generated_scene_enrichment_empty"),
        },
        "metrics": {
            key: metrics.get(key)
            for key in ("total_seconds", "output_tokens", "decode_tokens_per_second")
        },
    }

def file_sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def get_url(url, timeout=120):
    # Use macOS curl's trusted system CA store. Python.org Python can lack an installed
    # certificate bundle, even when curl itself works. TLS verification remains enabled.
    p = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location",
         "--connect-timeout", "15", "--max-time", str(timeout),
         "--user-agent", "VCE-TextLLM-Benchmark/1.0", url],
        capture_output=True, timeout=timeout + 5
    )
    if p.returncode:
        raise RuntimeError("HTTPS download failed via curl: " + p.stderr.decode("utf-8", errors="replace")[-1200:])
    return p.stdout

def token_count(text, processor=None):
    tok = getattr(processor, "tokenizer", None) if processor is not None else None
    if tok is not None and callable(getattr(tok, "encode", None)):
        try:
            val = tok.encode(text, add_special_tokens=False)
            if hasattr(val, "tolist"): val = val.tolist()
            if val and isinstance(val[0], list): val = val[0]
            return len(val), "tokenizer.encode"
        except Exception: pass
    return max(1, round(len(re.findall(r"\w+|[^\w\s]", text)) * 1.25)) if text else 0, "estimated_lexical_proxy"

def github_token():
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t: return t.strip()
    try:
        p = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=8)
        if p.returncode == 0 and p.stdout.strip(): return p.stdout.strip()
    except Exception: pass
    raise RuntimeError("Не найден GitHub token. Один раз выполните gh auth login или задайте GITHUB_TOKEN с Contents: write.")

def gh_request(method, url, token, obj=None):
    # Keep normal TLS certificate verification; curl uses the macOS system trust store.
    cmd = ["curl", "--silent", "--show-error", "--location", "--request", method,
           "--connect-timeout", "15", "--max-time", "45",
           "--header", "Accept: application/vnd.github+json",
           "--header", "Authorization: Bearer " + token,
           "--header", "X-GitHub-Api-Version: 2022-11-28",
           "--header", "User-Agent: VCE-TextLLM-Benchmark/1.0",
           "--write-out", "\\n__HTTP_STATUS__%{http_code}"]
    data = None
    if obj is not None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        cmd += ["--header", "Content-Type: application/json", "--data-binary", "@-"]
    p = subprocess.run(cmd + [url], input=data, capture_output=True, timeout=50)
    if p.returncode:
        raise RuntimeError("GitHub HTTPS request failed via curl: " + p.stderr.decode("utf-8", errors="replace")[-1200:])
    try:
        raw, status = p.stdout.rsplit(b"\n__HTTP_STATUS__", 1)
        code = int(status.strip())
    except Exception as exc:
        raise RuntimeError("Could not parse GitHub API HTTP status from curl response.") from exc
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        body = {"message": raw.decode("utf-8", errors="replace")[:2000]}
    return code, body

def check_github(token):
    c,d=gh_request("GET",f"https://api.github.com/repos/{REPO}",token)
    if c!=200: raise RuntimeError(f"GitHub access check failed: HTTP {c}: {d}")
    if (d.get("permissions") or {}).get("push") is False: raise RuntimeError("GitHub token не имеет push-доступа к репозиторию.")

def upload_github(token, path, content, message):
    url=f"https://api.github.com/repos/{REPO}/contents/{path}"
    body={"message":message,"branch":BRANCH,"content":base64.b64encode(content).decode("ascii")}
    c,d=gh_request("PUT",url,token,body)
    if c not in (200,201): raise RuntimeError(f"Upload {path} failed: HTTP {c}: {d}")
    commit=d.get("commit") or {}
    return commit.get("html_url") or commit.get("sha")

def api_json(api, path, timeout=10):
    return json.loads(get_url(api.rstrip("/") + path, timeout).decode("utf-8"))

def analyze_video_once(video, api, outdir):
    print("[1/6] Проверка локального API VCE")
    spec=api_json(api,"/openapi.json",10)
    if "/api/v1/analyze" not in (spec.get("paths") or {}): raise RuntimeError("У API не найден /api/v1/analyze.")
    print("[2/6] Один проход по видео с выключенной финальной Text LLM")
    cmd=["curl","--silent","--show-error","--max-time","7200","-w","\n__VCE_HTTP_STATUS__%{http_code}",
         "-F","response_format=json","-F","text_llm_enabled=false","-F","generate_summary=true",
         "-F","generate_json=false","-F","generate_txt=false","-F","enable_stage_timing=true",
         "-F",f"video_file=@{video};type=video/mp4",api.rstrip("/")+"/api/v1/analyze"]
    p=subprocess.run(cmd,capture_output=True,text=True)
    if p.returncode: raise RuntimeError(f"curl/VCE failed ({p.returncode}): {p.stderr[-1800:]}")
    body,mark=p.stdout.rsplit("\n__VCE_HTTP_STATUS__",1)
    if int(mark.strip())>=400: raise RuntimeError(f"VCE HTTP {mark}: {body[:1500]}")
    data=json.loads(body)
    if not isinstance(data.get("segments"),list) or not data["segments"]: raise RuntimeError("VCE не вернул segments[].")
    write_json(outdir/"source_payload.local.json",data)
    print(f"    сцен: {len(data['segments'])}; базовая сводка: {len(str((data.get('meta') or {}).get('global_summary') or ''))} символов")
    return data

class State:
    def __init__(self,name,model,iteration,flags):
        self.name,self.model,self.iteration,self.flags=name,model,iteration,flags
        self.start=time.perf_counter(); self.load=0.; self.config=0.; self.prompt=0.; self.template=0.; self.parse=0.; self.reconcile=0.
        self.processor=None; self.prompt_text=""; self.formatted=""; self.in_tokens=None; self.in_method="unknown"; self.prompt_chars=0; self.prompt_started=None
        self.ttft=None; self.decode=0.; self.wall=0.; self.out_tokens=0; self.out_text=[]; self.calls=[]; self.repair=0; self.parse_fail=0
        self.stream_calls=0; self.generate_calls=0; self.fallback=0; self.stream_error=0; self.thinking_fallback=False; self.json_repair_helper=0
        self.prompt_rewrite=None; self.template_thinking=None; self.stream_thinking=None; self.primary_raw=""; self.result=None; self.error=None; self.drafter=None; self.draft_kind=None
        self.compact_info={}; self.draft_load=0.; self.quality_guard_flags=0

def function_supported(fn,key):
    try:
        sig=inspect.signature(fn)
        return key in sig.parameters or any(p.kind==inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    except Exception: return False

def load_source(outdir):
    print("[3/6] Загрузка закреплённого исходника VCE")
    src=get_url(SOURCE_URL).decode("utf-8")
    a=src.find("def run_text_llm_postprocess("); b=src.find("\ndef format_text_llm_report(",a)
    if a<0 or b<0: raise RuntimeError("В закреплённом исходнике не найдена run_text_llm_postprocess.")
    fn=src[a:b]
    patches=[
      ("    from mlx_vlm import load, generate","    from mlx_vlm import load as _real_load, generate as _real_generate\n    load, generate = BENCH_load, BENCH_generate"),
      ("    try:\n        from mlx_vlm.generate import stream_generate\n    except Exception:\n        stream_generate = None","    try:\n        from mlx_vlm.generate import stream_generate as _real_stream\n        stream_generate = BENCH_stream\n    except Exception:\n        stream_generate = None"),
      ("    from mlx_vlm.prompt_utils import apply_chat_template","    from mlx_vlm.prompt_utils import apply_chat_template as _real_template\n    apply_chat_template = BENCH_template"),
      ("    from mlx_vlm.utils import load_config","    from mlx_vlm.utils import load_config as _real_config\n    load_config = BENCH_config"),
      ("                from json_repair import repair_json","                from json_repair import repair_json as _real_repair\n                repair_json = BENCH_repair"),
      ("        limits = (600, 650, 650)","        BENCH.prompt_started = time.perf_counter()\n        limits = (600, 650, 650)"),
      ("        try:\n            # For this constrained extraction task, avoid spending time on long reasoning output.","        BENCH.prompt += max(0.0,time.perf_counter()-(BENCH.prompt_started or time.perf_counter()))\n        BENCH.prompt_text = instruction\n        try:\n            # For this constrained extraction task, avoid spending time on long reasoning output."),
      ("        try:\n            parsed = _text_llm_parse_json(raw)","        BENCH.primary_raw = raw\n        try:\n            parsed = _text_llm_parse_json(raw)"),
      ("        parsed = _vce_v4_finalize_analysis_payload(parsed, segments, existing_summary)","        parsed = _vce_v4_finalize_analysis_payload(parsed, segments, existing_summary)\n        parsed = BENCH_quality_guard(parsed, segments)"),
    ]
    for old,new in patches:
        if fn.count(old)!=1: raise RuntimeError("Unexpected source snapshot: expected exactly one patch point: "+old[:70])
        fn=fn.replace(old,new,1)
    path=outdir/"_instrumented"/"vce_snapshot.py"; path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(src[:a]+fn+src[b:],encoding="utf-8")
    name="_vce_text_llm_benchmark"
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod)
    for attr in ("run_text_llm_postprocess","_text_llm_parse_json"):
        if not hasattr(mod,attr): raise RuntimeError("Missing function after VCE import: "+attr)
    return mod

def install_hooks(mod, state):
    mlx=importlib.import_module("mlx_vlm"); gen=importlib.import_module("mlx_vlm.generate")
    promptmod=importlib.import_module("mlx_vlm.prompt_utils"); utilmod=importlib.import_module("mlx_vlm.utils")
    try: draftmod=importlib.import_module("mlx_vlm.speculative.drafters")
    except Exception: draftmod=None
    real_load=getattr(mlx,"load"); real_gen=getattr(mlx,"generate",getattr(gen,"generate",None)); real_stream=getattr(gen,"stream_generate",None)
    real_template=getattr(promptmod,"apply_chat_template"); real_config=getattr(utilmod,"load_config")
    s_think=function_supported(real_stream,"enable_thinking"); s_budget=function_supported(real_stream,"thinking_budget")
    s_draft=function_supported(real_stream,"draft_model"); s_draft_kind=function_supported(real_stream,"draft_kind")
    g_think=function_supported(real_gen,"enable_thinking")
    mod.BENCH=state
    mod.BENCH_quality_guard = lambda payload, segments: validate_text_llm_payload(payload, segments, state=state)
    def wload(*a,**k):
        t=time.perf_counter(); value=real_load(*a,**k); state.load+=time.perf_counter()-t
        try: state.processor=value[1]
        except Exception: pass
        return value
    def wconfig(*a,**k):
        t=time.perf_counter(); value=real_config(*a,**k); state.config+=time.perf_counter()-t; return value
    def wtemplate(processor,config,instruction,*a,**kwargs):
        t=time.perf_counter(); prompt=str(instruction); state.prompt_text=prompt
        if state.flags.get("fast") and "Scene_enrichment:" in prompt and "Исправь ТОЛЬКО синтаксис" not in prompt:
            old=prompt
            prompt,n1=re.subn(r"Scene_enrichment:.*?Uncertainties:","Scene_enrichment: верни пустой массив []; не переписывай сцены. Uncertainties:",prompt,count=1,flags=re.S)
            prompt,n2=re.subn(r'"scene_enrichment":\[\{.*?\}\],"uncertainties"','"scene_enrichment":[],"uncertainties"',prompt,count=1,flags=re.S)
            state.prompt_rewrite=(n1==1 and n2==1 and prompt!=old)
        if state.flags.get("thinking_off"): kwargs["enable_thinking"]=False
        try: result=real_template(processor,config,prompt,*a,**kwargs); state.template_thinking=kwargs.get("enable_thinking") if "enable_thinking" in kwargs else None
        except TypeError as e:
            if not state.flags.get("thinking_off") or not any(x in str(e) for x in ("enable_thinking","unexpected keyword")): raise
            kwargs.pop("enable_thinking",None); state.thinking_fallback=True; state.template_thinking=False
            result=real_template(processor,config,prompt,*a,**kwargs)
        state.formatted=str(result); state.prompt_chars=len(state.formatted); state.in_tokens,state.in_method=token_count(state.formatted,processor); state.template+=time.perf_counter()-t; state.prompt_text=prompt
        return result
    def itemtext(x):
        v=getattr(x,"text",None)
        if v is None and isinstance(x,str): v=x
        return str(v) if v else ""
    def wstream(*a,**kwargs):
        state.stream_calls+=1
        if state.flags.get("thinking_off"):
            state.stream_thinking=(False if s_think else None)
            if s_think: kwargs["enable_thinking"]=False
            else: kwargs.pop("enable_thinking",None); state.thinking_fallback=True
            if not s_budget: kwargs.pop("thinking_budget",None)
        if state.flags.get("speculative"):
            if state.drafter is None or not s_draft or not s_draft_kind: raise RuntimeError("Speculative mode unavailable in active stream runtime.")
            kwargs["draft_model"]=state.drafter; kwargs["draft_kind"]=state.draft_kind
        def iterator():
            start=time.perf_counter(); first=None; text=[]; max_tok=0
            try:
                for item in real_stream(*a,**kwargs):
                    part=itemtext(item)
                    if part:
                        if first is None: first=time.perf_counter(); state.ttft=state.ttft if state.ttft is not None else first-start
                        text.append(part)
                    try: max_tok=max(max_tok,int(getattr(item,"generation_tokens",0) or 0))
                    except Exception: pass
                    yield item
            except TypeError as e:
                msg=str(e)
                if state.flags.get("thinking_off") and not text and any(k in msg for k in ("enable_thinking","thinking_budget")):
                    kw=dict(kwargs); kw.pop("enable_thinking",None); kw.pop("thinking_budget",None); state.thinking_fallback=True
                    for item in real_stream(*a,**kw):
                        part=itemtext(item)
                        if part:
                            if first is None: first=time.perf_counter(); state.ttft=state.ttft if state.ttft is not None else first-start
                            text.append(part)
                        try: max_tok=max(max_tok,int(getattr(item,"generation_tokens",0) or 0))
                        except Exception: pass
                        yield item
                else: state.stream_error+=1; raise
            except Exception: state.stream_error+=1; raise
            finally:
                end=time.perf_counter(); wall=end-start; dec=max(0.,end-first) if first is not None else wall
                txt="".join(text); n,_=token_count(txt,state.processor)
                state.decode+=dec; state.wall+=wall; state.out_tokens+=max(int(n or 0),max_tok); state.out_text.append(txt)
                state.calls.append({"kind":"stream","ttft_seconds":first-start if first else None,"decode_seconds":dec,"wall_seconds":wall,"output_tokens":max(int(n or 0),max_tok),"repair":False})
        return iterator()
    def wgenerate(*a,**kwargs):
        state.generate_calls+=1; prompt=kwargs.get("prompt",a[2] if len(a)>2 else ""); repair="Исправь ТОЛЬКО синтаксис" in str(prompt)
        if repair: state.repair+=1
        elif state.stream_calls: state.fallback+=1
        if state.flags.get("thinking_off"):
            if g_think: kwargs["enable_thinking"]=False
            else: kwargs.pop("enable_thinking",None); state.thinking_fallback=True
        t=time.perf_counter()
        try: value=real_gen(*a,**kwargs)
        except TypeError as e:
            if state.flags.get("thinking_off") and "enable_thinking" in str(e):
                kwargs.pop("enable_thinking",None); state.thinking_fallback=True; value=real_gen(*a,**kwargs)
            else: raise
        elapsed=time.perf_counter()-t; txt=value if isinstance(value,str) else str(getattr(value,"text",None) or value); n,_=token_count(txt,state.processor)
        state.decode+=elapsed; state.wall+=elapsed; state.out_tokens+=int(n or 0); state.out_text.append(txt)
        state.calls.append({"kind":"generate","ttft_seconds":None,"decode_seconds":elapsed,"wall_seconds":elapsed,"output_tokens":int(n or 0),"repair":repair})
        return value
    mod.BENCH_load=wload; mod.BENCH_generate=wgenerate; mod.BENCH_stream=wstream; mod.BENCH_template=wtemplate; mod.BENCH_config=wconfig
    if not hasattr(mod,"BENCH_real_repair"):
        try: mod.BENCH_real_repair=importlib.import_module("json_repair").repair_json
        except Exception: mod.BENCH_real_repair=lambda *a,**k: None
    def wrepair(*a,**k):
        state.json_repair_helper+=1
        return mod.BENCH_real_repair(*a,**k)
    mod.BENCH_repair=wrepair
    if not hasattr(mod,"BENCH_parse"): mod.BENCH_parse=mod._text_llm_parse_json
    orig_parse=mod.BENCH_parse
    def wparse(*a,**k):
        t=time.perf_counter()
        try: return orig_parse(*a,**k)
        except Exception: state.parse_fail+=1; raise
        finally: state.parse+=time.perf_counter()-t
    mod._text_llm_parse_json=wparse
    if hasattr(mod,"reconcile_text_llm_result"):
        if not hasattr(mod,"BENCH_reconcile"): mod.BENCH_reconcile=mod.reconcile_text_llm_result
        orig=mod.BENCH_reconcile
        def wreconcile(*a,**k):
            t=time.perf_counter()
            try: return orig(*a,**k)
            finally: state.reconcile+=time.perf_counter()-t
        mod.reconcile_text_llm_result=wreconcile
    return {"stream":real_stream,"draftmod":draftmod,"supports_draft":s_draft,"supports_draft_kind":s_draft_kind}

def compact_segments(segments):
    out=copy.deepcopy(segments); removed=0
    for seg in out:
        for key in ("audio_transcript","visual_description"):
            text=re.sub(r"\s+"," ",str(seg.get(key) or "")).strip(); parts=re.split(r"(?<=[.!?。！？])\s+",text); seen=set(); keep=[]
            for part in parts:
                k=re.sub(r"\s+"," ",part).casefold().strip()
                if k and k not in seen: seen.add(k); keep.append(part)
            new=" ".join(keep); removed+=max(0,len(text)-len(new)); seg[key]=new
        tags=seg.get("visual_tags") if isinstance(seg.get("visual_tags"),dict) else {}; desc=str(seg.get("visual_description") or "").casefold(); ocr=str(seg.get("ocr_text") or "").casefold(); clean={}
        for k,v in tags.items():
            value=str(v or "")
            if not re.search(r"\d",value) and len(value)>3 and (value.casefold() in desc or value.casefold() in ocr): removed+=len(str(k))+len(value); continue
            clean[k]=v
        seg["visual_tags"]=clean
    return out,{"evidence_chars_removed_approx":removed,"ocr_truncated":False,"scene_count":len(segments)}

def evidence(segments, summary):
    vals=[str(summary or "")]
    for s in segments:
        vals.extend(str(s.get(k) or "") for k in ("audio_transcript","ocr_text","visual_description"))
        vals.extend(str(v) for v in (s.get("visual_tags") or {}).values())
    return "\n".join(vals)

def audit(result, segments, summary, unchanged):
    """Universal structural checks only; semantics require source-aware review."""
    if not isinstance(result, dict):
        return {"valid_json_object": False, "valid_scene_ids": False,
                "source_segments_unmodified": unchanged, "critical_atom_coverage": None}
    valid = set()
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        try:
            valid.add(int(segment.get("scene_id", index + 1)))
        except (TypeError, ValueError):
            pass
    invalid = []
    for key in ("key_points", "scene_enrichment"):
        for item in result.get(key, []) if isinstance(result.get(key), list) else []:
            if not isinstance(item, dict):
                invalid.append(f"{key}:not_object")
                continue
            try:
                if int(item.get("scene_id")) not in valid:
                    invalid.append(item.get("scene_id"))
            except (TypeError, ValueError):
                invalid.append(item.get("scene_id"))
    scene_ids = []
    for item in result.get("scene_enrichment", []) if isinstance(result.get("scene_enrichment"), list) else []:
        if isinstance(item, dict):
            try:
                scene_ids.append(int(item.get("scene_id")))
            except (TypeError, ValueError):
                pass
    missing_scene_enrichment = sorted(valid - set(scene_ids))
    duplicate_scene_enrichment = sorted({sid for sid in scene_ids if scene_ids.count(sid) > 1})
    valid_text = isinstance(result.get("executive_summary"), str) and bool(result.get("executive_summary").strip())
    return {
        "valid_json_object": True,
        "valid_scene_ids": not invalid and not missing_scene_enrichment and not duplicate_scene_enrichment,
        "invalid_scene_ids": invalid,
        "missing_scene_enrichment_ids": missing_scene_enrichment,
        "duplicate_scene_enrichment_ids": duplicate_scene_enrichment,
        "nonempty_executive_summary": valid_text,
        "source_segments_unmodified": unchanged,
        "critical_atom_count": None,
        "critical_atom_coverage": None,
        "critical_atoms_missing_count": None,
        "unsupported_numeric_claims_proxy": [],
        "semantic_grounding_checked": False,
        "manual_review_required": True,
    }

def prepare_drafter(st,hooks):
    dm=hooks.get("draftmod"); stream=hooks.get("stream")
    if dm is None or not hasattr(dm,"load_drafter"): return False,"mlx-vlm lacks speculative drafters.load_drafter"
    if stream is None or not hooks.get("supports_draft"): return False,"installed stream_generate has no draft_model parameter"
    if hooks.get("supports_draft_kind") is False: return False,"installed stream_generate has no draft_kind parameter; skip speculative test"
    t=time.perf_counter()
    try: val=dm.load_drafter(DRAFTER9)
    except Exception as e: return False,f"Could not load {DRAFTER9}: {type(e).__name__}: {e}"
    st.draft_load=time.perf_counter()-t
    if isinstance(val,tuple): st.drafter,st.draft_kind=val[0],val[1] if len(val)>1 else None
    else: st.drafter=val
    return True,"compatible drafter loaded"

def run_variant(mod,segments,summary,source,duration,name,model,iteration,flags):
    run_segments=copy.deepcopy(segments); compact_info={}
    if flags.get("compact"): run_segments,compact_info=compact_segments(run_segments)
    before=hashlib.sha256(json.dumps(segments,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    st=State(name,model,iteration,flags); st.compact_info=compact_info; hooks=install_hooks(mod,st)
    try:
        try: import mlx.core as mx; mx.clear_cache()
        except Exception: pass
        import gc; gc.collect()
        t=time.perf_counter()
        if flags.get("speculative"):
            ok,reason=prepare_drafter(st,hooks)
            if not ok: raise RuntimeError(reason)
        result=mod.run_text_llm_postprocess(model_path=model,source_name=source,video_duration=duration,existing_summary=summary,segments=run_segments,max_tokens=4096,progress_callback=None)
        st.result=result if isinstance(result,dict) else None
        if st.result is None: raise RuntimeError("Text LLM did not return an object")
    except Exception as e: st.error=f"{type(e).__name__}: {e}"
    finally:
        st.total=time.perf_counter()-locals().get("t",st.start)
        after=hashlib.sha256(json.dumps(segments,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        q=audit(st.result,segments,summary,before==after)
        raw_obj = parse_primary_model_output(st.primary_raw)
        raw_enrich = raw_obj.get("scene_enrichment") if isinstance(raw_obj, dict) else None
        out_tokens=st.out_tokens; primary=next((c for c in st.calls if c.get("kind")=="stream"),None); rate=out_tokens/st.decode if st.decode>0 else None
        think_tokens=0
        for txt in st.out_text:
            match=re.search(r"<think>(.*?)</think>",txt,flags=re.I|re.S)
            if match: think_tokens+=token_count(match.group(1),st.processor)[0] or 0
        metrics={"variant":name,"model":model,"iteration":iteration,"success":st.error is None and st.result is not None,"error":st.error,
          "total_seconds":round(st.total,4),"model_load_seconds":round(st.load,4),"config_load_seconds":round(st.config,4),"draft_load_seconds":round(st.draft_load,4),
          "prompt_build_seconds":round(st.prompt,4),"chat_template_seconds":round(st.template,4),"input_tokens":st.in_tokens,"input_token_count_method":st.in_method,
          "prompt_characters":st.prompt_chars,"time_to_first_token_seconds":round(st.ttft,4) if st.ttft is not None else None,
          "prefill_seconds_proxy":round(st.ttft,4) if st.ttft is not None else None,"decode_seconds":round(st.decode,4),
          "output_tokens":out_tokens,"primary_output_tokens":primary.get("output_tokens") if primary else None,
          "primary_output_near_token_limit":bool(primary and primary.get("output_tokens",0)>=4094),
          "decode_tokens_per_second":round(rate,3) if rate else None,"thinking_tokens_observed":think_tokens,
          "thinking_off_requested":bool(flags.get("thinking_off")),"thinking_off_applied_both":bool(flags.get("thinking_off") and st.template_thinking is False and st.stream_thinking is False and not st.thinking_fallback),"thinking_fallback_used":st.thinking_fallback,"stream_calls":st.stream_calls,
          "generate_calls":st.generate_calls,"stream_errors":st.stream_error,"nonstream_fallbacks":st.fallback,"json_repair_passes":st.repair,"json_repair_helper_calls":st.json_repair_helper,
          "json_parse_failures":st.parse_fail,"json_parse_seconds":round(st.parse,4),"reconciliation_seconds":round(st.reconcile,4),
          "primary_raw_json_parseable":isinstance(raw_obj,dict),"generated_scene_enrichment_count":len(raw_enrich) if isinstance(raw_enrich,list) else None,
          "generated_scene_enrichment_empty":raw_enrich==[] if isinstance(raw_enrich,list) else None,"prompt_rewrite_ok":st.prompt_rewrite,"compaction":compact_info or None,"quality_guard_flags":st.quality_guard_flags,
          "quality":q,"call_metrics":st.calls}
        result_out=st.result or {}
        st.processor=None; st.drafter=None
        try: import mlx.core as mx; mx.clear_cache()
        except Exception: pass
        import gc; gc.collect()
    return metrics, result_out, raw_obj

def med_agg(runs):
    agg={}
    for name,values in runs.items():
        ok=[x for x in values if x.get("success")]; row={"runs":len(values),"successful_runs":len(ok)}
        for key in ("total_seconds","model_load_seconds","config_load_seconds","prompt_build_seconds","chat_template_seconds","input_tokens","time_to_first_token_seconds","decode_seconds","output_tokens","decode_tokens_per_second","quality_guard_flags"):
            vals=[r[key] for r in ok if isinstance(r.get(key),(int,float))]
            row[key+"_median"]=round(statistics.median(vals),4) if vals else None
        cov=[r.get("quality",{}).get("critical_atom_coverage") for r in ok if isinstance(r.get("quality",{}).get("critical_atom_coverage"),(int,float))]
        row["critical_atom_coverage_median"]=round(statistics.median(cov),4) if cov else None
        row["all_json_valid"]=bool(ok) and all(r.get("quality",{}).get("valid_json_object") for r in ok)
        row["all_scene_ids_valid"]=bool(ok) and all(r.get("quality",{}).get("valid_scene_ids") for r in ok)
        row["source_preserved_all"]=bool(ok) and all(r.get("quality",{}).get("source_segments_unmodified") for r in ok)
        row["thinking_off_applied_all"]=bool(ok) and all(r.get("thinking_off_applied_both") is True for r in ok) if name=="A_thinking_off" else None
        row["prompt_rewrite_ok_all"]=bool(ok) and all(r.get("prompt_rewrite_ok") is True for r in ok) if name=="B_fast_summary" else None
        row["generated_enrichment_empty_all"]=bool(ok) and all(r.get("generated_scene_enrichment_empty") is True for r in ok) if name=="B_fast_summary" else None
        row["runs_with_quality_guard_flags"]=sum(1 for r in ok if int(r.get("quality_guard_flags",0) or 0)>0)
        agg[name]=row
    return agg

def delta(base,candidate):
    if not isinstance(base,(int,float)) or not isinstance(candidate,(int,float)) or base<=0: return {"improvement_pct":None,"interpretation":"not measured"}
    p=(base-candidate)/base*100
    return {"baseline_seconds":base,"candidate_seconds":candidate,"improvement_pct":round(p,3),"outside_5pct_noise":abs(p)>NOISE,
            "interpretation":"material (>5%)" if abs(p)>NOISE else "within test noise (<=5%)"}

def maybe_use_vce_python():
    if importlib.util.find_spec("mlx_vlm") is not None: return
    try: ps=subprocess.run(["ps","-axo","pid=,command="],capture_output=True,text=True,timeout=5).stdout.splitlines()
    except Exception: ps=[]
    for row in ps:
        low=row.lower()
        if not any(x in low for x in ("videocontext","uvicorn","main:app","vce_server")): continue
        m=re.search(r"(/\S*python(?:\d(?:\.\d+)?)?)",row)
        if not m: continue
        py=m.group(1)
        if not Path(py).is_file() or Path(py).resolve()==Path(sys.executable).resolve(): continue
        try:
            p=subprocess.run([py,"-c","import mlx_vlm; print('ok')"],capture_output=True,text=True,timeout=10)
            if p.returncode==0 and "ok" in p.stdout:
                os.execve(py,[py,str(Path(__file__).resolve()),*sys.argv[1:]],os.environ.copy())
        except Exception: pass

def benchmark_variants(focused_quality=False):
    """Return the 9B variants to run; focused mode keeps baseline and Fast Summary."""
    all_variants = [
        ("baseline", MODEL9, {}),
        ("A_thinking_off", MODEL9, {"thinking_off": True}),
        ("B_fast_summary", MODEL9, {"fast": True}),
        ("C_compact_context", MODEL9, {"compact": True}),
    ]
    if focused_quality:
        return [all_variants[0], all_variants[2]]
    return all_variants


def comparison_reference_variant(best_9b_variant: str, *, focused_quality: bool = False) -> str:
    """Use the full-output baseline as the 4B reference in focused quality mode.

    Fast Summary intentionally removes per-scene generation, so using its flags
    for D would not compare equivalent report-generation tasks.
    """
    return "baseline" if focused_quality else best_9b_variant


def flags_for_variant(variants, variant_name: str) -> dict:
    for name, _model, flags in variants:
        if name == variant_name:
            return dict(flags)
    raise ValueError(f"Benchmark variant is missing: {variant_name}")


def main():
    ap=argparse.ArgumentParser(description="Run VCE Text LLM optimization benchmarks with quality-first guard.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {SCRIPT_VERSION}")
    ap.add_argument("--video",type=Path,default=VIDEO_DEFAULT); ap.add_argument("--api-url",default=os.getenv("VCE_API_URL","http://127.0.0.1:7555"))
    ap.add_argument("--repeats",type=int,default=3); ap.add_argument("--output-dir",type=Path)
    ap.add_argument("--input-json",type=Path,help="Reuse existing VCE JSON with segments[] and skip video analysis.")
    ap.add_argument("--skip-4b",action="store_true"); ap.add_argument("--skip-speculative",action="store_true")
    ap.add_argument("--focused-quality",action="store_true",help="Run baseline + 9B Fast Summary + 4B Fast Summary; skip A/C/E for a targeted quality rerun.")
    ap.add_argument("--no-upload",action="store_true"); ap.add_argument("--no-upload-outputs",action="store_true")
    args=ap.parse_args()
    if args.focused_quality:
        args.skip_speculative = True
        if args.skip_4b:
            ap.error("--focused-quality requires the 4B comparison; do not combine with --skip-4b")
    if not 1<=args.repeats<=10: ap.error("--repeats must be 1..10")
    run_id=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid.uuid4().hex[:6]
    outdir=(args.output_dir or (Path.home()/"Downloads"/"vce_text_llm_benchmarks"/run_id)).expanduser().resolve(); outdir.mkdir(parents=True,exist_ok=True)
    token=None
    if not args.no_upload: token=github_token(); check_github(token)
    try:
        maybe_use_vce_python()
        if args.input_json:
            payload=json.loads(args.input_json.read_text(encoding="utf-8"))
            if not isinstance(payload.get("segments"),list) or not payload["segments"]: raise RuntimeError("--input-json needs segments[]")
            print("[1/6] Использую существующий VCE JSON; видео повторно не обрабатывается.")
        else:
            if not args.video.is_file(): raise RuntimeError(f"Не найден видеофайл: {args.video}")
            payload=analyze_video_once(args.video.resolve(),args.api_url,outdir)
        segments=payload["segments"]; meta=payload.get("meta") or {}; summary=str(meta.get("global_summary") or "")
        source=Path(str(meta.get("source") or args.video.name)).name; duration=float(meta.get("duration") or 0)
        input_hash=hashlib.sha256(json.dumps(segments,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if not importlib.util.find_spec("mlx_vlm"): raise RuntimeError("mlx-vlm не импортируется. Активируйте venv VideoContext Engine, затем запустите скрипт.")
        mod=load_source(outdir)
        variants=benchmark_variants(args.focused_quality)
        runs={}; outputs={}; flat=[]
        if args.focused_quality:
            print("[4/6] Focused quality: baseline + B_fast_summary; A/C/E пропускаются, затем B сравнивается с 4B.")
        else:
            print("[4/6] Baseline + A/B/C: одинаковые исходные сцены, по",args.repeats,"повтора.")
        for name,model,flags in variants:
            runs[name]=[]; outputs[name]=[]
            for n in range(1,args.repeats+1):
                print(f"  {name} {n}/{args.repeats} ...",flush=True)
                m,o,raw_model_output=run_variant(mod,segments,summary,source,duration,name,model,n,dict(flags)); runs[name].append(m); flat.append(m)
                if m.get("success"): outputs[name].append(build_output_record(n,o,raw_model_output,m))
                write_json(outdir/"runs.partial.local.json",flat)
                print("    ",m.get("total_seconds"),"s; success=",m.get("success"),"tokens=",m.get("output_tokens"))
        agg=med_agg(runs); base=agg.get("baseline",{}).get("total_seconds_median"); base_cov=agg.get("baseline",{}).get("critical_atom_coverage_median")
        candidates=[]
        for name in ("A_thinking_off","B_fast_summary","C_compact_context"):
            r=agg.get(name,{})
            good=r.get("successful_runs",0)>0 and r.get("all_json_valid") and r.get("all_scene_ids_valid") and r.get("source_preserved_all") and r.get("runs_with_quality_guard_flags",0)==0
            if name=="A_thinking_off": good=good and r.get("thinking_off_applied_all")
            if name=="B_fast_summary": good=good and r.get("prompt_rewrite_ok_all") and r.get("generated_enrichment_empty_all")
            cov=r.get("critical_atom_coverage_median")
            if base_cov is not None and cov is not None and cov<base_cov-0.05: good=False
            if good and isinstance(r.get("total_seconds_median"),(int,float)): candidates.append((r["total_seconds_median"],name))
        if not candidates: candidates=[(agg[n]["total_seconds_median"],n) for n in ("A_thinking_off","B_fast_summary","C_compact_context") if agg.get(n,{}).get("successful_runs") and isinstance(agg[n].get("total_seconds_median"),(int,float)) and agg[n].get("all_json_valid") and agg[n].get("all_scene_ids_valid") and agg[n].get("source_preserved_all") and agg[n].get("runs_with_quality_guard_flags",0)==0]
        best=min(candidates)[1] if candidates else "baseline"
        comparison_reference = comparison_reference_variant(best, focused_quality=args.focused_quality)
        bestflags = flags_for_variant(variants, comparison_reference)
        if args.focused_quality and comparison_reference != best:
            print(
                "[5/6] Для честного 4B quality-сравнения использую baseline-профиль: "
                "Fast Summary отключает генерацию scene_enrichment."
            )
        print("[5/6] Выбранный 9B вариант для D/E:",comparison_reference)
        best = comparison_reference
        if not args.skip_4b:
            name="D_Qwen3_5_4B_vs_"+best; runs[name]=[]; outputs[name]=[]
            for n in range(1,args.repeats+1):
                print(f"  {name} {n}/{args.repeats} ...",flush=True)
                m,o,raw_model_output=run_variant(mod,segments,summary,source,duration,name,MODEL4,n,dict(bestflags)); m["reference_9b_variant"]=best
                runs[name].append(m); flat.append(m)
                if m.get("success"): outputs[name].append(build_output_record(n,o,raw_model_output,m))
                write_json(outdir/"runs.partial.local.json",flat)
        speculative={"status":"skipped","reason":"Disabled with --skip-speculative" if args.skip_speculative else "Not yet checked"}
        if not args.skip_speculative:
            name="E_speculative_vs_"+best; runs[name]=[]; outputs[name]=[]; reasons=[]
            for n in range(1,args.repeats+1):
                print(f"  {name} {n}/{args.repeats} (runtime compatibility checked before loading drafter)...",flush=True)
                flags=dict(bestflags); flags["speculative"]=True
                m,o,raw_model_output=run_variant(mod,segments,summary,source,duration,name,MODEL9,n,flags); runs[name].append(m); flat.append(m)
                if m.get("success"): outputs[name].append(build_output_record(n,o,raw_model_output,m))
                elif m.get("error"): reasons.append(m["error"])
                write_json(outdir/"runs.partial.local.json",flat)
            n_ok=sum(bool(x.get("success")) for x in runs[name])
            speculative={"status":"completed" if n_ok else "skipped_or_failed","successful_runs":n_ok,"reason":reasons[:3] or "compatible drafter loaded"}
        agg=med_agg(runs); deltas={}
        for name,row in agg.items():
            if name=="baseline": continue
            ref=best if name.startswith(("D_","E_")) else "baseline"
            deltas[name]={"reference_variant":ref,**delta(agg.get(ref,{}).get("total_seconds_median"),row.get("total_seconds_median"))}
        manifest={"run_id":run_id,"benchmark_script_version":SCRIPT_VERSION,"output_provenance_schema_version":"raw-and-final-output-v1","started_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"video_basename":args.video.name if not args.input_json else source,
           "video_sha256":file_sha(args.video) if not args.input_json else None,"scene_count":len(segments),"repeats":args.repeats,"source_snapshot_sha":SOURCE_SHA,
           "noise_threshold_pct":NOISE,"python":sys.version,"platform":platform.platform(),"machine":platform.machine(),"focused_quality_run":bool(args.focused_quality),"quality_guard_enabled":True,"raw_video_and_vce_scene_payload_uploaded":False,"input_segments_sha256_local_only":input_hash}
        doc={"manifest":manifest,"models":{"baseline":MODEL9,"4b":MODEL4,"speculative_target":MODEL9,"speculative_drafter":DRAFTER9},"aggregates":agg,"speed_deltas":deltas,
          "best_9b_variant_for_D_E":best,"four_b_comparison_reference_variant":best,"speculative_decoding":speculative,"threshold_pct":NOISE,
          "quality_policy":{"timing_differences_within_5pct_are_noise":True,"manual_review_required":True},"runs":flat}
        public=copy.deepcopy(doc); public["manifest"].pop("input_segments_sha256_local_only",None)
        outputs_doc={"manifest":{k:v for k,v in manifest.items() if k!="input_segments_sha256_local_only"},
          "warning":"Generated Text LLM outputs are published; raw video and raw scene/transcript/OCR input are not published.","outputs_by_variant":outputs}
        write_json(outdir/"metrics.local.json",doc); write_json(outdir/"metrics.json",public); write_json(outdir/"llm_outputs.json",outputs_doc)
        csvpath=outdir/"runs.csv"; fields=["variant","model","iteration","success","total_seconds","model_load_seconds","config_load_seconds","input_tokens","input_token_count_method","time_to_first_token_seconds","decode_seconds","output_tokens","decode_tokens_per_second","json_repair_passes","json_repair_helper_calls","quality_guard_flags","error"]
        with csvpath.open("w",encoding="utf-8-sig",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(flat)
        lines=["# VCE Text LLM benchmark","",f"- Run: {run_id}",f"- Scenes: {len(segments)}",f"- Repeats: {args.repeats}",f"- Focused quality run: {bool(args.focused_quality)}",f"- Video-agnostic non-mutating integrity guard: enabled",f"- D/E 9B reference profile: {best}",f"- Timing deltas within ±{NOISE:.0f}% count as test noise.","",
          "> Raw video and raw scene/transcript/OCR input remain local. Generated Text LLM outputs are uploaded separately.","",
          "| Variant | Successful | Total median (s) | Load median (s) | Input tokens | TTFT (s) | Decode (s) | Output tokens | tok/s | Source fact grounding | Runs with integrity flags |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for n,r in agg.items():
            def fmt(k):
                v=r.get(k); return "—" if v is None else f"{v:.2f}"
            grounding = "not evaluated" if r.get("critical_atom_coverage_median") is None else f"{r['critical_atom_coverage_median']:.1%}"
            lines.append(f"| {n} | {r.get('successful_runs')}/{r.get('runs')} | {fmt('total_seconds_median')} | {fmt('model_load_seconds_median')} | {fmt('input_tokens_median')} | {fmt('time_to_first_token_seconds_median')} | {fmt('decode_seconds_median')} | {fmt('output_tokens_median')} | {fmt('decode_tokens_per_second_median')} | {grounding} | {r.get('runs_with_quality_guard_flags',0)} |")
        lines += ["","## Interpretation notes","",
          "- TTFT/prefill is stream start to first non-empty text chunk, a proxy rather than an internal MLX kernel timer.",
          "- Token counts use the model tokenizer when available; otherwise metrics mark a lexical estimate.",
          "- Decode tok/s uses output tokens / time after the first chunk when streaming metrics are available.",
          "- Fast Summary is accepted only when prompt/schema rewriting applied and raw model JSON has an empty scene_enrichment list. VCE downstream reconciliation may repopulate that field deterministically.",
          "- Speculative decoding runs only if installed runtime accepts draft_model/draft_kind and the Qwen3.5-9B MTP drafter loads. Otherwise the reason is recorded.",
          "- The default benchmark validates JSON, scene IDs, scene completeness and source preservation. It does not infer domain-specific facts or semantic correctness; manually review claims against source evidence.",""]
        report="\n".join(lines); (outdir/"report.md").write_text(report,encoding="utf-8")
        print("\n[6/6] Resultat:\n"+report)
        if not args.no_upload:
            remote=f"artifacts/text-llm-benchmarks/{run_id}"
            uploads={"metrics.json":(outdir/"metrics.json").read_bytes(),"runs.csv":csvpath.read_bytes(),"report.md":report.encode()}
            if not args.no_upload_outputs: uploads["llm_outputs.json"]=(outdir/"llm_outputs.json").read_bytes()
            print("Публикую результаты на GitHub:",f"https://github.com/{REPO}/tree/{BRANCH}/{remote}")
            for name,blob in uploads.items():
                url=upload_github(token,remote+"/"+name,blob,f"bench: publish Text LLM results {run_id}")
                print("  uploaded",name,url)
            write_json(outdir/"upload_receipt.json",{"remote_directory":f"https://github.com/{REPO}/tree/{BRANCH}/{remote}","files":list(uploads)})
            print("GitHub upload complete:",f"https://github.com/{REPO}/tree/{BRANCH}/{remote}")
        print("Локальные результаты:",outdir); print("Raw video and original scene/transcript/OCR payload were not uploaded.")
        return 0
    except KeyboardInterrupt:
        print("Остановлено пользователем; частичные измерения сохранены локально.",file=sys.stderr); return 130
    except Exception as e:
        write_json(outdir/"fatal_error.local.json",{"run_id":run_id,"error":f"{type(e).__name__}: {e}"})
        print(f"FATAL: {type(e).__name__}: {e}",file=sys.stderr); print("Логи сохранены в",outdir,file=sys.stderr); return 1

if __name__=="__main__":
    raise SystemExit(main())
