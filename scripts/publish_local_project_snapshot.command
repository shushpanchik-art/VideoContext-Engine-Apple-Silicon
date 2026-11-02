#!/bin/bash
set -Eeuo pipefail
REPO="shushpanchik-art/VideoContext-Engine-Apple-Silicon"
BRANCH="feat/vk-url-batch-ingestion-20261102"
PROJECT="$HOME/VideoContext-Engine"
fail(){ echo "ОШИБКА: $*" >&2; exit 1; }
[[ -f "$PROJECT/VideoContextEngine_v3.19.py" ]] || fail "Не найден VideoContextEngine_v3.19.py в $PROJECT"
[[ -f "$PROJECT/videocontext_web_ui.html" ]] || fail "Не найден videocontext_web_ui.html в $PROJECT"
command -v git >/dev/null || fail "Git не установлен"
command -v python3 >/dev/null || fail "python3 не найден"
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then gh auth setup-git >/dev/null 2>&1 || true; fi
WORK="$(/usr/bin/mktemp -d /tmp/vce-publication.XXXXXX)"
trap '/bin/rm -rf "$WORK"' EXIT
echo "[1/4] Получаю ветку PR #15..."
git clone --quiet --depth 1 --branch "$BRANCH" "https://github.com/$REPO.git" "$WORK/repo" || fail "Не удалось клонировать ветку PR"
cd "$WORK/repo"
echo "[2/4] Копирую runtime и готовлю обезличенный конфиг..."
python3 - "$PROJECT" "$WORK/repo" <<'PY'
import os,re,sys,json,shutil
from pathlib import Path
src,dst=map(lambda x:Path(x).resolve(),sys.argv[1:])
core=("VideoContextEngine_v3.19.py","videocontext_web_ui.html")
home=str(Path.home())
for n in core:
    if not (src/n).is_file(): raise SystemExit("Нет файла "+n)
    t=(src/n).read_text(encoding="utf-8")
    if re.search(r"\b(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})",t):
        raise SystemExit("В "+n+" похожий на секрет токен. Push остановлен.")
    if home in t: raise SystemExit("В "+n+" найден персональный путь. Push остановлен.")
    shutil.copy2(src/n,dst/n)
skip_dirs={".git",".venv","venv","env","__pycache__",".pytest_cache",".mypy_cache",".ruff_cache",".cache","cache","caches","models","model","model_cache","models_cache","checkpoints","hf_cache","huggingface","whisper_cache","mlx_cache","node_modules","site-packages","temp","tmp","downloads","Downloads","logs","log","outputs","output","benchmark_results"}
skip_ext={".mp4",".m4v",".mov",".mkv",".webm",".avi",".mpeg",".mpg",".mp3",".m4a",".wav",".aac",".flac",".part",".ytdl",".sqlite",".db",".pyc",".pyo",".bak",".orig",".swp",".tmp"}
secrets=re.compile(r"\b(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{20,})")
secret_key=re.compile(r"(^|[_-])(api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|cookie|authorization|credential)([_-]|$)",re.I)
added=list(core); excluded=[]
for root,dirs,files in os.walk(src,followlinks=False):
    root=Path(root); dirs[:]=[d for d in dirs if d not in skip_dirs and not d.lower().startswith(".backup") and ".backup" not in d.lower() and ".bak" not in d.lower() and not (root/d).is_symlink()]
    for fn in files:
        p=root/fn
        if p.is_symlink() or not p.is_file(): continue
        rel=p.relative_to(src); low=fn.lower()
        if rel.as_posix() in core: continue
        if low in {".ds_store",".env","credentials","credentials.json","cookies.txt","id_rsa","id_ed25519","auth.json"} or low.startswith(".env.") or p.suffix.lower() in skip_ext or low.endswith((".log",".worker.json","_context.txt","_context.json")) or any(".bak" in part.lower() or ".backup" in part.lower() or ".before_" in part.lower() for part in rel.parts):
            excluded.append(str(rel)); continue
        if p.stat().st_size>50*1024*1024: excluded.append(str(rel)+" [over 50MB]"); continue
        if low=="videocontext_settings.json":
            try: obj=json.loads(p.read_text(encoding="utf-8"))
            except Exception: excluded.append(str(rel)+" [invalid JSON]"); continue
            def clean(v,k=""):
                if k and secret_key.search(k): return "<REDACTED>"
                if isinstance(v,dict): return {a:clean(b,str(a)) for a,b in v.items()}
                if isinstance(v,list): return [clean(x) for x in v]
                if isinstance(v,str):
                    v=v.replace(home,"~")
                    v=secrets.sub("<REDACTED>",v)
                    return re.sub(r"(://)[^/@\s:]+:[^/@\s]+@",r"\1<REDACTED>@",v)
                return v
            out=dst/"config"/"videocontext_settings.local-example.json"; out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps(clean(obj),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            added.append("config/videocontext_settings.local-example.json"); continue
        target=dst/rel
        if target.exists(): excluded.append(str(rel)+" [repo copy retained]"); continue
        data=p.read_bytes()
        try:
            txt=data.decode("utf-8")
            if secrets.search(txt): excluded.append(str(rel)+" [possible credential]"); continue
            data=txt.replace(home,"~").encode("utf-8")
        except UnicodeDecodeError: pass
        target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data); shutil.copystat(p,target); added.append(str(rel))
if not (dst/"requirements.txt").exists():
    (dst/"requirements.txt").write_text("fastapi\nuvicorn[standard]\nopencv-python\nyt-dlp\npillow\nnumpy\nopenai-whisper\nhuggingface_hub\n",encoding="utf-8"); added.append("requirements.txt")
(dst/"requirements-apple-silicon.txt").write_text("mlx-vlm\ntorchvision\nocrmac\ncertifi\n",encoding="utf-8"); added.append("requirements-apple-silicon.txt")
doc="""# Clean macOS installation (Apple Silicon)

Install Homebrew Python and FFmpeg:
    brew install python ffmpeg

Clone the public repository and install dependencies:
    git clone https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon.git ~/VideoContext-Engine
    cd ~/VideoContext-Engine
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python -m pip install -r requirements-apple-silicon.txt
    cp config/videocontext_settings.apple_silicon.example.json videocontext_settings.json

Start the engine:
    python VideoContextEngine_v3.19.py

Open http://127.0.0.1:7555/ui (API docs: http://127.0.0.1:7555/docs).
Model weights download on first use and are not included in Git.
The repository contains source and native launcher files, not a prebuilt signed app.
Private settings, credentials, models, caches, local videos, reports, logs and backups are excluded.
See UPSTREAM.md and NOTICE.md for attribution and license notes.
"""
d=dst/"docs"/"INSTALL_FROM_PUBLIC_REPO.md"; d.parent.mkdir(parents=True,exist_ok=True); d.write_text(doc,encoding="utf-8"); added.append("docs/INSTALL_FROM_PUBLIC_REPO.md")
r=dst/"README.md"; marker="<!-- VCE_FULL_RUNTIME_SOURCE_NOTICE -->"
if r.exists():
    s=r.read_text(encoding="utf-8")
    if marker not in s:
        r.write_text(marker+"\n> **Полный runtime включён:** VideoContextEngine_v3.19.py и videocontext_web_ui.html. Установка: docs/INSTALL_FROM_PUBLIC_REPO.md.\n\n"+s,encoding="utf-8"); added.append("README.md")
manifest={"included_or_updated":added,"excluded_local_files":excluded,"settings_redacted":"config/videocontext_settings.local-example.json" in added}
(dst/"PUBLICATION_PACKAGE_MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print("Добавлено/обновлено:",len(added),"; исключено/пропущено:",len(excluded))
for p in added: print(" +",p)
PY
echo "[3/4] Коммичу..."
git add -A
if ! git diff --cached --quiet; then
  if ! git config user.name >/dev/null; then
    if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
      LOGIN="$(gh api user --jq .login)"; UID="$(gh api user --jq .id)"
      git config user.name "$LOGIN"; git config user.email "$UID+$LOGIN@users.noreply.github.com"
    else
      git config user.name "VideoContext Engine Publisher"; git config user.email "vce-publisher@users.noreply.github.com"
    fi
  fi
  git commit -m "Publish complete VideoContext Engine runtime and safe settings example"
else
  echo "Изменений нет."
fi
echo "[4/4] Отправляю изменения в ветку PR #15..."
git push origin "HEAD:refs/heads/$BRANCH" || fail "Push не прошёл. Выполни gh auth login, затем повтори."
echo "ГОТОВО. PR обновлён: https://github.com/$REPO/pull/15"
echo "Коммит: $(git rev-parse --short HEAD)"
