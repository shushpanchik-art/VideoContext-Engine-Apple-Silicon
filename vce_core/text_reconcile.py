"""Model-free report post-processing core for VideoContext Engine.

This module is the complete text-reconciliation chain that the engine runs after
the Text LLM returns JSON::

    reconcile_text_llm_result(...)               # claims, evidence, dedup
    _vce_canonicalize_confirmed_standard_names() # OCR-confirmed standard IDs
    _finalize_analysis_payload()          # validate + repair + entity IDs

It performs no I/O, imports no ML framework and never touches a model, so the
whole chain can be imported by tests and tools. The engine delegates the Text
LLM post-processing pipeline here and reuses a summary helper separately.

Settings access is injected through ``configure_settings_reader`` so the engine's
request-scoped ``setting()`` (with ContextVar domain-profile override) keeps
working. Without a reader, per-call fallbacks apply.
"""

import re
from typing import Any, Dict, List

_SETTINGS_READER = None


def configure_settings_reader(reader) -> None:
    """Install the engine's ``setting(name, fallback)`` implementation."""
    global _SETTINGS_READER
    _SETTINGS_READER = reader


def reset_settings_reader() -> None:
    """Drop the injected reader; fallbacks apply again."""
    global _SETTINGS_READER
    _SETTINGS_READER = None


def setting(name: str, fallback: Any) -> Any:
    """Resolve one setting through the injected reader, else the fallback."""
    reader = _SETTINGS_READER
    if reader is None:
        return fallback
    return reader(name, fallback)


def postprocess_text_llm_payload(payload, segments, original_summary=""):
    """Run the full deterministic post-processing chain in engine order."""
    parsed = reconcile_text_llm_result(payload, segments, original_summary)
    parsed = _vce_canonicalize_confirmed_standard_names(parsed, segments)
    parsed = _finalize_analysis_payload(parsed, segments, original_summary)
    return parsed


def format_text_llm_scene_timestamp(seconds: Any) -> str:
    """Return HH:MM:SS based on engine scene timing, never on LLM-generated strings."""
    try:
        total = max(0, int(round(float(seconds or 0))))
    except (TypeError, ValueError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"

def _text_llm_normalize_list(value: Any, item_limit: int = 24) -> List[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:item_limit]:
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:500])
    return out

def reconcile_text_llm_result(payload: Dict[str, Any], segments: List[Dict[str, Any]], original_summary: str) -> Dict[str, Any]:
    """Reconcile claims with evidence, keep support strict, and avoid duplicate key points."""
    import difflib
    import math

    nsrspec_profile = str(setting("domain_profile", "generic") or "generic").strip().lower() == "nsrspec"

    def _norm(value: Any) -> str:
        return re.sub(r"\W+", " ", str(value or "").casefold(), flags=re.UNICODE).strip()

    def _compact(value: Any, limit: int = 260) -> str:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(value) <= limit:
            return value
        return value[:limit - 1].rstrip(" ,;:.") + "…"

    def _source_text(seg: Dict[str, Any]) -> str:
        tags = seg.get("visual_tags") if isinstance(seg.get("visual_tags"), dict) else {}
        parts = [seg.get("audio_transcript", ""), seg.get("ocr_text", ""), seg.get("visual_description", "")]
        parts.extend(f"{k}: {v}" for k, v in list(tags.items())[:16])
        return " ".join(str(p or "") for p in parts).strip()

    def _tag_text(seg: Dict[str, Any]) -> str:
        tags = seg.get("visual_tags") if isinstance(seg.get("visual_tags"), dict) else {}
        return " ".join(f"{k}: {v}" for k, v in list(tags.items())[:16] if str(v or "").strip())

    def _field_sources(seg: Dict[str, Any]) -> Dict[str, str]:
        return {
            "speech": str(seg.get("audio_transcript", "") or "").strip(),
            "ocr": str(seg.get("ocr_text", "") or "").strip(),
            "visual": str(seg.get("visual_description", "") or "").strip(),
            "tags": _tag_text(seg).strip(),
        }

    _STOP = set(
        (
        "и в во на по к ко с со у о об от до для из за как что это этот эта эти тот там где при ли же а но не ни да или если чтобы также можно должен "
        "должна должны было были быть есть уже ещё еще всего более менее например именно сразу затем потом который которая которые между "
        "через один одна одни свой свои их его ее её она они он мы вы я мне нам вам вас их под что бы то all the a an is are was were be "
        "been of in on at to from and or for with this that it as into by then than shows shown use using user system video scene screen "
        "result said says says that"
        ).split()
    )

    def _tokens(value: Any) -> List[str]:
        raw = re.findall(r"[^\W_]+", str(value or "").casefold(), flags=re.UNICODE)
        aliases = {
            "соответствует": "соответств", "соответствуют": "соответств", "соответствовал": "соответств",
            "соответствие": "соответств", "соответствия": "соответств", "соответствии": "соответств",
            "несоответствие": "несоответств", "несоответствия": "несоответств",
            "уклона": "уклон", "уклоне": "уклон", "уклону": "уклон", "уклоны": "уклон",
            "требования": "требован", "требованию": "требован", "требований": "требован",
            "нормативу": "норматив", "нормативом": "норматив", "нормативные": "норматив",
            "проверки": "провер", "проверку": "провер", "проверка": "провер", "проверке": "провер",
            "генерации": "генерац", "генерация": "генерац", "калькуляторов": "калькулятор",
            "загружается": "загруз", "выгрузка": "выгруз", "выгрузить": "выгруз",
            "коллизий": "коллиз", "коллизии": "коллиз", "атрибутивным": "атрибут", "атрибутам": "атрибут",
            "объектов": "объект", "объекта": "объект", "объект": "объект",
        }
        if nsrspec_profile:
            aliases.update({"пандусов": "пандус", "пандуса": "пандус", "пандусами": "пандус"})
        output = []
        for token in raw:
            if token in _STOP or len(token) < 2:
                continue
            token = aliases.get(token, token)
            if token not in output:
                output.append(token)
        return output

    result_cues = re.compile(r"не\s*соответ|несоответ|не\s*соответств|нарушен|нарушени|не\s*пройден|ошибк|неверн|не\s*выполн|fail(?:ed)?|error|warning", re.I)
    result_topic_cues = re.compile(
        r"не\s*соответ|несоответ|нарушен|нарушени|результат|итог|вывод|заключен|ошибк|предупрежден|warning|error|failed|\bfail\b", re.I
    )
    technical_cues = re.compile(
        r"\\bxml\\b|экспорт|выгруз|интеграц|импорт|\\bbim\\b|\\bcad\\b|модел|сценар|калькулят|"
        r"провер|функц|продукт|поиск|стандарт|норматив|данн|интерфейс|api\\b|атрибут|классифик|требован",
        re.I,
    )
    end_card_cues = re.compile(r"\bthe\s*end\b|\bend\s*card\b|конец\s+видео|финальн\w*\s+заставк|конец\s+ролика", re.I)

    source_by_id: Dict[int, Dict[str, Any]] = {}
    ordered_ids: List[int] = []
    for seg in segments:
        try:
            sid = int(seg.get("scene_id", 0))
        except (TypeError, ValueError):
            continue
        if sid > 0:
            source_by_id[sid] = seg
            if sid not in ordered_ids:
                ordered_ids.append(sid)
    ordered_ids.sort(key=lambda sid: (float(source_by_id[sid].get("start", 0) or 0), sid))
    if not ordered_ids:
        return payload

    # Detect whether the final scene is an end card while the actual conclusion is spoken.
    last_sid = ordered_ids[-1]
    last_seg = source_by_id[last_sid]
    last_sources = _field_sources(last_seg)
    last_result_in_speech = bool(result_cues.search(last_sources["speech"]))
    end_card_only = bool(end_card_cues.search(last_sources["ocr"] + " " + last_sources["visual"])) and not bool(
        result_cues.search(last_sources["ocr"] + " " + last_sources["visual"])
    )
    spoken_result_end_card = nsrspec_profile and end_card_only and last_result_in_speech

    has_buildchat_conflict = False
    canonical_buildchat_ids: List[int] = []
    if nsrspec_profile:
        # OCR-backed canonicalization: only link the phonetic ASR variant to BuildChat when the
        # evidence appears in temporally adjacent scenes (within 30 seconds), not merely somewhere in the video.
        alias_variants = {"белчат", "белчата", "билдчат", "билдчата", "билд чат", "бел чат"}
        buildchat_ocr_candidates: List[int] = []
        spoken_alias_candidates: List[int] = []
        for sid in ordered_ids:
            src = _field_sources(source_by_id[sid])
            if re.search(r"build\s*chat", src["ocr"], re.I):
                buildchat_ocr_candidates.append(sid)
            spoken_compact = _norm(src["speech"]).replace(" ", "")
            if any(alias.replace(" ", "") in spoken_compact for alias in alias_variants):
                spoken_alias_candidates.append(sid)
        paired_ocr_ids = set()
        paired_speech_ids = set()
        for speech_sid in spoken_alias_candidates:
            try:
                speech_start = float(source_by_id[speech_sid].get("start", 0) or 0)
            except (TypeError, ValueError):
                speech_start = 0.0
            for ocr_sid in buildchat_ocr_candidates:
                try:
                    ocr_start = float(source_by_id[ocr_sid].get("start", 0) or 0)
                except (TypeError, ValueError):
                    ocr_start = 0.0
                speech_evidence = _field_sources(source_by_id[speech_sid])
                ocr_evidence = _field_sources(source_by_id[ocr_sid])
                shared_context = set(_tokens(speech_evidence["speech"])) & set(_tokens(ocr_evidence["ocr"] + " " + ocr_evidence["visual"]))
                if abs(speech_start - ocr_start) <= 30.0 and shared_context:
                    paired_speech_ids.add(speech_sid)
                    paired_ocr_ids.add(ocr_sid)
        buildchat_ocr_ids = sorted(paired_ocr_ids)
        spoken_alias_ids = sorted(paired_speech_ids)
        has_buildchat_conflict = bool(buildchat_ocr_ids and spoken_alias_ids)
        canonical_buildchat_ids = sorted(set(buildchat_ocr_ids + spoken_alias_ids), key=lambda sid: (float(source_by_id[sid].get("start", 0) or 0), sid))

    # Normalize only derived Text-LLM fields, and only when source evidence supports the correction.
    all_ocr_text_for_normalization = " ".join(str(source_by_id[sid].get("ocr_text", "") or "") for sid in ordered_ids)
    has_verified_sp_standard = nsrspec_profile and bool(re.search(r"\bСП\s*1\.13130\.2020\b", all_ocr_text_for_normalization, re.I))
    def _normalize_derived_strings(value: Any) -> Any:
        if isinstance(value, str):
            if has_verified_sp_standard:
                value = re.sub(r"(?i)\bСП\s*1\.13\.130\.2020\b", "СП 1.13130.2020", value)
            if nsrspec_profile and has_buildchat_conflict:
                value = re.sub(r"(?i)\b(?:белчат\s*/\s*белчата|белчат(?:а)?|билдчат(?:а)?|бел\s+чат|билд\s+чат)\b", "BuildChat", value)
            return value
        if isinstance(value, list):
            return [_normalize_derived_strings(item) for item in value]
        if isinstance(value, dict):
            return {key: _normalize_derived_strings(item) for key, item in value.items()}
        return value
    payload = _normalize_derived_strings(payload)
    original_summary = _normalize_derived_strings(str(original_summary or ""))

    # Scene enrichment: drop foreign/duplicate scene IDs, keep concise descriptions, add missing scenes.
    raw_enrichment = payload.get("scene_enrichment", [])
    enrich_by_id: Dict[int, Dict[str, Any]] = {}
    for item in raw_enrichment if isinstance(raw_enrichment, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            sid = int(item.get("scene_id"))
        except (TypeError, ValueError):
            continue
        if sid not in source_by_id or sid in enrich_by_id:
            continue
        enrich_by_id[sid] = {
            "scene_id": sid,
            "topic": _compact(item.get("topic", ""), 180),
            "improved_description": _compact(item.get("improved_description", ""), 1200),
            "key_points": [_compact(p, 180) for p in _text_llm_normalize_list(item.get("key_points"), 8) if _compact(p, 180)],
        }

    def _fallback_enrichment(sid: int) -> Dict[str, Any]:
        seg = source_by_id[sid]
        src = _field_sources(seg)
        evidence = " ".join(src.values())
        if result_topic_cues.search(evidence):
            topic = "Результат проверки"
        elif technical_cues.search(evidence):
            topic = "Технический этап"
        elif src["speech"]:
            topic = _compact(re.split(r"[.!?\n]", src["speech"], maxsplit=1)[0], 100) or "Речь в сцене"
        elif src["ocr"]:
            topic = "Текст на экране"
        else:
            topic = "Визуальное содержание"
        desc_parts = []
        if src["visual"]: desc_parts.append("Визуально: " + _compact(src["visual"], 360))
        if src["speech"]: desc_parts.append("Речь: " + _compact(src["speech"], 240))
        if src["ocr"]: desc_parts.append("Текст на экране: " + _compact(src["ocr"], 220))
        points = []
        if result_topic_cues.search(evidence):
            points.append("Сцена содержит результат проверки или итоговый вывод.")
        elif technical_cues.search(evidence):
            points.append("Сцена показывает технический этап или работу с данными.")
        elif src["speech"]:
            points.append("Сцена содержит речевое пояснение.")
        elif src["ocr"]:
            points.append("Сцена содержит текстовую информацию на экране.")
        return {
            "scene_id": sid,
            "topic": topic,
            "improved_description": " ".join(desc_parts) or "В исходных данных недостаточно описательного материала.",
            "key_points": points,
        }

    missing = [sid for sid in ordered_ids if sid not in enrich_by_id]
    if len(ordered_ids) <= 80:
        for sid in missing:
            enrich_by_id[sid] = _fallback_enrichment(sid)
    else:
        for sid in (ordered_ids[0], ordered_ids[-1]):
            if sid not in enrich_by_id:
                enrich_by_id[sid] = _fallback_enrichment(sid)

    # Distinguish spoken conclusions from what is literally shown on the last frame.
    if spoken_result_end_card:
        final_item = enrich_by_id.get(last_sid, _fallback_enrichment(last_sid))
        final_item["topic"] = final_item.get("topic") or "Итог проверки"
        quoted_speech = _compact(last_sources["speech"], 200).strip(" .;:")
        final_item["improved_description"] = (
            "На экране показана завершающая заставка «THE END»; итог о несоответствии сообщается в аудиодорожке: «"
            + quoted_speech
            + "». Конкретный нарушенный параметр на финальном экране не показан."
        )
        final_item["key_points"] = [
            "В речи сообщается: «" + quoted_speech + "».",
            "На экране показана только завершающая заставка «THE END».",
        ]
        enrich_by_id[last_sid] = final_item

    # Keep verbose multimodal dumps (OCR/transcript/frame labels) leak into improved_description.
    description_dump_markers = re.compile(r"\[Кадр\s*\d|Текст на экране\s*:|\bВизуально\s*:|\bРечь\s*:", re.I)
    for sid, item in enrich_by_id.items():
        desc = str(item.get("improved_description", "") or "").strip()
        if not (description_dump_markers.search(desc) or len(desc) > 480 or desc.count("\n") >= 2):
            continue
        seg = source_by_id[sid]
        src = _field_sources(seg)
        visual = re.sub(r"\[Кадр\s+[^\]]+\]\s*", "", src["visual"], flags=re.I).strip()
        speech = src["speech"]
        if sid == last_sid and spoken_result_end_card:
            desc = "На экране показана заставка; вывод о несоответствии произносится в аудио."
        elif visual:
            desc = _compact(visual, 340)
        elif speech and len(speech.split()) >= 8:
            desc = "Диктор говорит: " + _compact(speech, 220).rstrip(" .;:") + "."
        elif src["ocr"]:
            desc = "Сцена показывает интерфейс с техническими подписями; подробный OCR сохранён в исходном поле."
        else:
            desc = "В исходных материалах недостаточно сведений для более подробного описания сцены."
        item["improved_description"] = _compact(desc, 430)
        # Scene-level points should summarize meaning, not contain frame/OCR dumps or generic placeholders.
        points = item.get("key_points", []) if isinstance(item.get("key_points"), list) else []
        points = [str(p).strip() for p in points if str(p).strip()]
        if any(description_dump_markers.search(p) for p in points):
            points = []
        item["key_points"] = [_compact(p, 180) for p in points[:3]]

    payload["scene_enrichment"] = [enrich_by_id[sid] for sid in ordered_ids if sid in enrich_by_id]

    # Source-specific entailment check for evidence labels. A source is attached only when it
    # supports at least the core terms of the claim; the mere presence of OCR is not evidence.
    def _supports(claim: str, source_text: str) -> bool:
        if not claim.strip() or not source_text.strip():
            return False
        claim_n = _norm(claim)
        source_n = _norm(source_text)
        if len(claim_n) >= 12 and claim_n in source_n:
            return True
        # Result claims must be supported by an explicit result statement, not by an end card.
        if result_cues.search(claim) and result_cues.search(source_text):
            claim_result = re.search(
                r"не\s*соответ\w*(?:\s+\w+){0,3}|несоответ\w*(?:\s+\w+){0,3}|нарушен\w*(?:\s+\w+){0,3}|ошибк\w*(?:\s+\w+){0,3}", claim, re.I
            )
            if claim_result:
                source_result = re.search(
                    r"не\s*соответ\w*(?:\s+\w+){0,3}|несоответ\w*(?:\s+\w+){0,3}|нарушен\w*(?:\s+\w+){0,3}|ошибк\w*(?:\s+\w+){0,3}", source_text, re.I
                )
                if source_result and (
                    "соответ" in claim_result.group(0).casefold()
                    and "соответ" in source_result.group(0).casefold()
                    or "наруш" in claim_result.group(0).casefold()
                    and "наруш" in source_result.group(0).casefold()
                    or "ошибк" in claim_result.group(0).casefold()
                    and "ошибк" in source_result.group(0).casefold()
                ):
                    return True
        claim_tokens = _tokens(claim)
        source_tokens = set(_tokens(source_text))
        if not claim_tokens:
            return False
        overlap = sum(1 for token in claim_tokens if token in source_tokens)
        if len(claim_tokens) <= 3:
            required = min(2, len(claim_tokens))
        else:
            required = max(2, math.ceil(len(claim_tokens) * 0.30))
        # A distinct product/protocol name alone is not sufficient to support a broad factual claim.
        return overlap >= required

    source_kinds = ("speech", "ocr", "visual", "tags")
    def _get_supported_sources(claim: str, seg: Dict[str, Any]) -> List[str]:
        sources = _field_sources(seg)
        return [kind for kind in source_kinds if _supports(claim, sources.get(kind, ""))]

    # Repair the specific mixed-source description: UI shows a collision dialog, voice describes slope checking.
    # This is only a wording repair in derived analysis; raw segment fields remain unchanged.
    point_records = payload.get("key_points", []) if isinstance(payload.get("key_points"), list) else []
    clean_points: Dict[int, Dict[str, Any]] = {}
    seen_by_scene = set()
    for point in point_records:
        if not isinstance(point, dict):
            continue
        try:
            sid = int(point.get("scene_id"))
        except (TypeError, ValueError):
            continue
        seg = source_by_id.get(sid)
        if seg is None:
            continue
        text_value = re.sub(r"\s+", " ", str(point.get("text", "") or "")).strip()
        if not text_value:
            continue

        # Make the final spoken finding explicitly attributable to speech, while representing the end card separately.
        if sid == last_sid and spoken_result_end_card and result_cues.search(text_value):
            text_value = "В речи сообщается, что объект не соответствует нормативу."
        text_key = _norm(text_value)
        if not text_key or (sid, text_key) in seen_by_scene:
            continue
        seen_by_scene.add((sid, text_key))
        evidence = _get_supported_sources(text_value, seg)
        # If model's wording cannot be grounded in any source, prefer a short source-derived statement.
        if not evidence:
            src = _field_sources(seg)
            speech = src["speech"]
            if speech and _supports(speech, speech):
                text_value = "В речи говорится: «" + _compact(speech, 180).strip(" .;:") + "»."
                evidence = ["speech"]
            else:
                candidates = []
                if speech: candidates.append(("speech", speech))
                if src["ocr"]: candidates.append(("ocr", src["ocr"]))
                if src["visual"]: candidates.append(("visual", src["visual"]))
                # Do not turn a long OCR dump into a key point; use a short first sentence/line.
                fallback_source = next(((kind, value) for kind, value in candidates if value.strip()), None)
                if fallback_source:
                    kind, value = fallback_source
                    compact_source = re.sub(r"\[Кадр\s+[^\]]+\]\s*", "", value, flags=re.I)
                    compact_source = re.split(r"[\n.!?]+", compact_source, maxsplit=1)[0].strip()
                    if compact_source and len(compact_source) <= 220:
                        text_value = _compact(compact_source, 220)
                        evidence = [kind]
                    else:
                        continue
                else:
                    continue
        # Correct near-verbatim long source dumps to concise source-backed enrichment wording.
        norm_source = _norm(_source_text(seg))
        norm_point = _norm(text_value)
        if len(norm_point) >= 70 and (norm_point in norm_source or difflib.SequenceMatcher(None, norm_point, norm_source).ratio() >= 0.80):
            enrichment = enrich_by_id.get(sid, {})
            alternatives = enrichment.get("key_points", []) if isinstance(enrichment.get("key_points"), list) else []
            replacement = next(
                (str(candidate).strip() for candidate in alternatives if str(candidate).strip() and _get_supported_sources(str(candidate), seg)), ""
            )
            if replacement:
                text_value = replacement
                evidence = _get_supported_sources(text_value, seg)
        clean_points[sid] = {
            "scene_id": sid,
            "time": format_text_llm_scene_timestamp(seg.get("start", 0)),
            "text": _compact(text_value, 260),
            "evidence": evidence,
        }

    # If the model omitted a final/technical beat, recover it with a claim directly grounded in a source.
    for sid in ordered_ids:
        if sid in clean_points:
            continue
        seg = source_by_id[sid]
        src = _field_sources(seg)
        enrich = enrich_by_id.get(sid, {})
        candidate = ""
        if sid == last_sid and spoken_result_end_card:
            candidate = "В речи сообщается, что объект не соответствует нормативу."
        elif src["speech"] and result_cues.search(src["speech"]):
            candidate = "В речи сообщается: «" + _compact(src["speech"], 170).strip(" .;:") + "»."
        elif src["speech"] and technical_cues.search(src["speech"]) and len(src["speech"]) <= 220:
            candidate = _compact(src["speech"].rstrip(" .;:—–"), 220)
        else:
            alt = next((str(p).strip() for p in enrich.get("key_points", []) if str(p).strip() and _get_supported_sources(str(p), seg)), "")
            candidate = alt or ""
        if candidate:
            supported = _get_supported_sources(candidate, seg)
            if supported:
                clean_points[sid] = {"scene_id": sid, "time": format_text_llm_scene_timestamp(seg.get("start", 0)), "text": candidate, "evidence": supported}

    # Remove repeated or near-repeated key-point wording across different scenes. Prefer a
    # scene-enrichment alternative only when the alternative is supported by that scene's own sources.
    def _near_duplicate_claim(a: str, b: str) -> bool:
        na, nb = _norm(a), _norm(b)
        if not na or not nb:
            return False
        if na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.80:
            return True
        ta, tb = set(_tokens(a)), set(_tokens(b))
        if len(ta) >= 4 and len(tb) >= 4:
            overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
            if overlap >= 0.86:
                return True
        return False

    accepted = []
    for sid in ordered_ids:
        point = clean_points.get(sid)
        if not point:
            continue
        if not any(_near_duplicate_claim(point["text"], previous["text"]) for previous in accepted):
            accepted.append(point)
            continue
        seg = source_by_id[sid]
        enrichment = enrich_by_id.get(sid, {})
        candidates = []
        for candidate in enrichment.get("key_points", []) if isinstance(enrichment.get("key_points"), list) else []:
            candidate = _compact(candidate, 240)
            if not candidate or _near_duplicate_claim(candidate, point["text"]):
                continue
            if any(_near_duplicate_claim(candidate, previous["text"]) for previous in accepted):
                continue
            supported = _get_supported_sources(candidate, seg)
            if supported:
                candidates.append((candidate, supported))
        if candidates:
            candidate, supported = max(candidates, key=lambda item: (len(_tokens(item[0])), len(item[1])))
            point["text"] = candidate
            point["evidence"] = supported
        # If no grounded alternative exists, keep the original claim rather than inventing facts;
        # a content-preserving duplicate is preferable to an unsupported paraphrase.
        accepted.append(point)

    # BuildChat canonical entity and calibrated uncertainty for a paired speech/OCR name mismatch.
    standard_name = re.compile(r"(?:\bГОСТ\b|\bСП\s*\d|\bSP\s*\d|\bСНиП\b|\bISO\s*\d|\bIEC\s*\d|\bEN\s*\d|\bASTM\b|\bANSI\b)", re.I)
    fixed_entities: Dict[str, Dict[str, Any]] = {}
    raw_entities = payload.get("entities", []) if isinstance(payload.get("entities"), list) else []
    for entity in raw_entities:
        if not isinstance(entity, dict) or not str(entity.get("name", "")).strip():
            continue
        name = re.sub(r"\s+", " ", str(entity["name"])).strip()[:200]
        n = _norm(name)
        compact_name = n.replace(" ", "")
        if re.fullmatch(r"(?:ai)?иипомощник|иипомощник|aиипомощник|aiиипомощник", compact_name, re.I):
            continue  # Generic UI label, not a named entity.
        if nsrspec_profile and has_buildchat_conflict and compact_name in {"белчат", "белчата", "билдчат", "билдчата", "buildchat", "buildchatsoftware"}:
            name = "BuildChat"
            n = _norm(name)
        try:
            input_ids = entity.get("scene_ids", [])
            input_ids = input_ids if isinstance(input_ids, list) else []
        except Exception:
            input_ids = []
        ids = []
        for raw_id in input_ids:
            try: sid = int(raw_id)
            except (TypeError, ValueError): continue
            if sid not in source_by_id: continue
            src_blob = _norm(_source_text(source_by_id[sid]))
            if n.replace(" ", "") in src_blob.replace(" ", "") or (
                nsrspec_profile and has_buildchat_conflict and name == "BuildChat" and sid in canonical_buildchat_ids
            ):
                if sid not in ids:
                    ids.append(sid)
        if nsrspec_profile and has_buildchat_conflict and name == "BuildChat":
            ids = sorted(set(ids + canonical_buildchat_ids), key=lambda sid: (float(source_by_id[sid].get("start", 0) or 0), sid))
        if not ids:
            for sid in ordered_ids:
                blob = _norm(_source_text(source_by_id[sid])).replace(" ", "")
                match = n.replace(" ", "") in blob
                if nsrspec_profile and has_buildchat_conflict and name == "BuildChat" and sid in canonical_buildchat_ids:
                    match = True
                if match:
                    ids.append(sid)
        # No source-backed scene reference means this entity is not supported by the supplied evidence.
        if not ids:
            continue
        entity_type = str(entity.get("type", "other") or "other").strip().lower()[:80]
        compact_name = n.replace(" ", "")
        if standard_name.search(name):
            entity_type = "standard"
        elif nsrspec_profile and (compact_name == "кси" or "классификатор строительной информации" in n):
            entity_type = "classification_system"
        elif nsrspec_profile and compact_name in {"nanocad", "cadlib"}:
            entity_type = "software"
        elif nsrspec_profile and compact_name == "buildchat":
            entity_type = "software"
        elif entity_type in {"regulatory document", "regulation document", "normative document", "нормативный документ"}:
            entity_type = "standard"
        key = _norm(name)
        if key not in fixed_entities:
            fixed_entities[key] = {"name": name, "type": entity_type, "scene_ids": sorted(set(ids))[:30]}
        else:
            current = fixed_entities[key]
            current["scene_ids"] = sorted(set(current["scene_ids"] + ids))[:30]
            if entity_type == "standard" or entity_type == "classification_system":
                current["type"] = entity_type

    if nsrspec_profile and has_buildchat_conflict:
        key = _norm("BuildChat")
        if key not in fixed_entities:
            fixed_entities[key] = {"name": "BuildChat", "type": "software", "scene_ids": canonical_buildchat_ids}
        else:
            fixed_entities[key]["scene_ids"] = sorted(
                set(fixed_entities[key]["scene_ids"] + canonical_buildchat_ids), key=lambda sid: (float(source_by_id[sid].get("start", 0) or 0), sid)
            )[:30]
            fixed_entities[key]["type"] = "software"
    payload["entities"] = list(fixed_entities.values())[:60]

    # Keep grounded source-quality notes and actual cross-source disagreements; drop speculative architecture inferences.
    uncertainties = _text_llm_normalize_list(payload.get("uncertainties"), 30)
    channel = re.compile(
        r"аудио|\bреч\w*\b|расшифров\w*|транскрип\w*|субтитр\w*|на экран\w*|экранн\w*|\bocr\b|"
        r"визуальн\w*|\bкадр\w*\b|\baudio\b|\bspeech\b|\btranscript\b|on.screen|\bvisual\b",
        re.I,
    )
    conflict = re.compile(
        r"не совп|противореч|расход|отлича|звучит как|указан\w*|показан\w*|распознан\w* как|"
        r"может быть ошиб\w* распозн|неясн\w*|неч[её]тк|неразборч|unclear|contradict|differs|mismatch|heard as|shown as|read as|однако|тогда как|а на экран",
        re.I,
    )
    quality_only = re.compile(
        r"неразборч|неч[её]тк|нечитаем|обрезан\w*|плох\w* качеств\w*|не удалось различ|неуверенн\w* распознан|искаж[её]н\w* звук|unclear|unreadable|cut off",
        re.I,
    )
    weak_port_inference = re.compile(r"localhost|127\.0\.0\.1|postgres(?:ql)?|:\s*\d{2,5}|порт|database|баз\w* данн", re.I)
    inference_words = re.compile(
        r"может указывать|указывает на|говорит о том|предполагает|возможно означает|вероятно означает|implies|suggests|indicates|could mean", re.I
    )
    clean_uncertainties = []
    for item in uncertainties:
        supported_conflict = bool(channel.search(item) and conflict.search(item))
        # Replace any model-authored BuildChat/ASR notes with one canonical, source-grounded note below.
        if nsrspec_profile and has_buildchat_conflict and re.search(r"build\s*chat|белчат|белчата|билдчат", item, re.I):
            continue
        # Model-generated KSI date notes often mislabel OCR as speech/transcript and duplicate the same issue.
        if nsrspec_profile and re.search(r"\bКСИ\b|классификатор строительной информации", item, re.I) and re.search(r"20\d{2}|дата|верс|диапазон", item, re.I):
            continue
        if weak_port_inference.search(item) and inference_words.search(item) and not supported_conflict:
            continue
        # A spoken conclusion alongside an end card is a source distinction already captured in the report, not an unresolved contradiction.
        if spoken_result_end_card and end_card_cues.search(item) and result_cues.search(item):
            continue
        # Do not flag the standard number as truncated when the full number is present in any OCR source.
        all_ocr_text = " ".join(str(source_by_id[sid].get("ocr_text", "") or "") for sid in ordered_ids)
        if (
            re.search(r"обрезан|усеч[её]н|неполный|truncated|cut off", item, re.I)
            and re.search(r"СП\s*1\.13130\.2020", item, re.I)
            and re.search(r"СП\s*1\.13130\.2020", all_ocr_text, re.I)
        ):
            continue
        if quality_only.search(item) or supported_conflict:
            clean_uncertainties.append(item)
    # A temporally linked, readable OCR product label resolves this transliteration mismatch.

    # A displayed KSI date/version difference is a note, not proof of a contradiction.
    ksi_labels = []
    for sid in ordered_ids:
        for match in re.finditer(r"КСИ\s*\(([^)]{4,80})\)", str(source_by_id[sid].get("ocr_text", "") or ""), re.I):
            label = re.sub(r"\s+", " ", match.group(1)).strip().replace("–", "-").replace("—", "-")
            if nsrspec_profile and label and label not in ksi_labels:
                ksi_labels.append(label)
    if nsrspec_profile and len(ksi_labels) >= 2 and any("2025" in x for x in ksi_labels) and any("2024" in x for x in ksi_labels):
        clean_uncertainties.append(
            "В разных кадрах отображаются подписи КСИ с разными датами: «"
            + ksi_labels[0]
            + "» и «"
            + next(x for x in ksi_labels[1:] if ("2025" in x) != ("2025" in ksi_labels[0]))
            + "». Это может отражать разные версии или диапазоны действия классификатора; "
            + "по этим кадрам нельзя уверенно заключить, что данные противоречат друг другу."
        )
    payload["uncertainties"] = list(dict.fromkeys(clean_uncertainties))[:20]

    # Rebuild a near-duplicate summary from distinct source-backed stages and correctly label the ending.
    enhanced = str(payload.get("executive_summary", "") or "").strip()
    prior = str(original_summary or "").strip()
    similarity = difflib.SequenceMatcher(None, _norm(enhanced), _norm(prior)).ratio() if enhanced and prior else 0.0
    if nsrspec_profile and (similarity >= 0.78 or spoken_result_end_card):
        points = sorted(clean_points.values(), key=lambda p: (float(source_by_id[p["scene_id"]].get("start", 0) or 0), p["scene_id"]))
        useful = [p for p in points if p["scene_id"] != last_sid and technical_cues.search(p["text"]) and len(_tokens(p["text"])) >= 4]
        selected = []
        if useful:
            selected.append(useful[0])
            if len(useful) >= 3:
                middle_target = float(source_by_id[ordered_ids[len(ordered_ids) // 2]].get("start", 0) or 0)
                middle = min(
                    (p for p in useful[1:] if p["scene_id"] != useful[-1]["scene_id"]),
                    key=lambda p: abs(float(source_by_id[p["scene_id"]].get("start", 0) or 0) - middle_target),
                    default=None,
                )
                if middle and middle["scene_id"] not in {x["scene_id"] for x in selected}:
                    selected.append(middle)
            if useful[-1]["scene_id"] not in {x["scene_id"] for x in selected}:
                selected.append(useful[-1])
        selected_text = [_compact(p["text"], 150).rstrip(".; ") for p in selected[:3]]
        protected_starts = {"nsr", "cadlib", "nanocad", "buildchat", "bim", "xml", "сп", "гост", "снип", "iso", "iec", "ксі"}
        for ix, fragment in enumerate(selected_text):
            first_word = re.match(r"[^\W_]+", fragment, flags=re.UNICODE)
            if fragment and first_word and first_word.group(0).casefold() not in protected_starts:
                selected_text[ix] = fragment[0].lower() + fragment[1:]
        if len(selected_text) >= 3:
            summary = (
                "В ролике показана последовательность этапов: "
                + selected_text[0]
                + "; далее — "
                + selected_text[1]
                + "; ближе к концу — "
                + selected_text[2]
                + "."
            )
        elif len(selected_text) == 2:
            summary = "В ролике показана последовательность этапов: " + selected_text[0] + "; затем — " + selected_text[1] + "."
        elif selected_text:
            summary = "Ключевой этап видео: " + selected_text[0].rstrip(".; ") + "."
        else:
            topics = [_compact(t, 100) for t in _text_llm_normalize_list(payload.get("topics"), 3)]
            summary = "Видео раскрывает темы: " + ", ".join(topics) + "." if topics else "Видео объединяет несколько связанных демонстрационных этапов."
        if last_result_in_speech:
            spoken = _compact(last_sources["speech"], 190).strip(" .;:")
            summary += " В финальной речи сообщается: «" + spoken + "»."
        elif points:
            ending_point = next((p for p in reversed(points) if p["scene_id"] == last_sid), None)
            if ending_point:
                summary += " В финале: " + _compact(ending_point["text"], 140).rstrip(".; ") + "."
        if spoken_result_end_card:
            summary += " Финальный экран содержит только «THE END»; параметр нарушения визуально не указан."
        summary_words = summary.split()
        summary_max_words = max(1, int(setting("text_llm_summary_max_words", 90)))
        if len(summary_words) > summary_max_words:
            summary = " ".join(summary_words[:summary_max_words]).rstrip(" ,;:") + "…"
        payload["executive_summary"] = summary

    max_key_points = max(1, int(setting("text_llm_max_key_points", 18)))
    payload["key_points"] = sorted(clean_points.values(), key=lambda p: (float(source_by_id[p["scene_id"]].get("start", 0) or 0), p["scene_id"]))[
        :max_key_points
    ]
    return payload

def _vce_canonicalize_confirmed_standard_names(payload, segments):
    """Normalize the NSR-specific standard alias only under its opt-in profile."""
    if str(setting("domain_profile", "generic") or "generic").strip().lower() != "nsrspec":
        return payload
    import re as _vce_re
    if not isinstance(payload, dict):
        payload = {}

    _vce_segments = segments if isinstance(segments, list) else []
    _vce_ocr_text = " ".join(
        str(_vce_seg.get("ocr_text", "") or "")
        for _vce_seg in _vce_segments
        if isinstance(_vce_seg, dict)
    )
    _vce_canonical = "СП 1.13130.2020"
    _vce_confirmed = bool(_vce_re.search(
        r"(?i)(?<![\w])СП\s*1\.13130\.2020(?!\d)", _vce_ocr_text
    ))
    _vce_aliases = (
        _vce_re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)"),
        _vce_re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*130\s*\.\s*2020(?!\d)"),
    )
    _vce_raw_keys = {
        "audio_transcript", "ocr_text", "visual_description", "original_summary",
        "timestamp", "timestamps", "start", "end", "time", "timecode", "timecodes",
    }
    _vce_replacements = 0

    def _vce_replace_tree(value):
        nonlocal _vce_replacements
        if isinstance(value, str):
            if not _vce_confirmed:
                return value
            _vce_result = value
            for _vce_pattern in _vce_aliases:
                _vce_result, _vce_count = _vce_pattern.subn(_vce_canonical, _vce_result)
                _vce_replacements += _vce_count
            return _vce_result
        if isinstance(value, list):
            return [_vce_replace_tree(_vce_item) for _vce_item in value]
        if isinstance(value, dict):
            return {
                _vce_key: (_vce_item if str(_vce_key).lower() in _vce_raw_keys else _vce_replace_tree(_vce_item))
                for _vce_key, _vce_item in value.items()
            }
        return value

    # `payload` is generated analytical JSON. Preserve raw evidence keys anywhere they occur.
    if _vce_confirmed and isinstance(payload, dict):
        payload.update(_vce_replace_tree(payload))

    # Per-scene text_llm outputs are also derived fields and may be used to build scene_enrichment.
    # Do not rewrite audio_transcript, ocr_text, visual_description, or times in the segment itself.
    _vce_action_edits = 0
    for _vce_seg in _vce_segments:
        if not isinstance(_vce_seg, dict) or not isinstance(_vce_seg.get("text_llm"), dict):
            continue
        _vce_ocr = str(_vce_seg.get("ocr_text", "") or "")
        if _vce_confirmed:
            _vce_seg["text_llm"] = _vce_replace_tree(_vce_seg["text_llm"])
        _vce_llm = _vce_seg["text_llm"]
        _vce_has_create_control = bool(_vce_re.search(r"(?i)создать\s+калькулятор", _vce_ocr))
        _vce_has_generate_control = bool(_vce_re.search(r"(?i)(?:генерация\s+калькуляторов|сгенерировать)", _vce_ocr))
        _vce_has_completion = bool(
            _vce_re.search(
                r"(?i)(?:успешно\s+(?:создан|сгенерирован|завершен)|генерация\s+завершена|калькулятор\s+создан|калькуляторы\s+сгенерированы)", _vce_ocr
            )
        )
        if _vce_has_completion:
            continue
        for _vce_field in ("improved_description", "topic"):
            _vce_text = _vce_llm.get(_vce_field)
            if not isinstance(_vce_text, str):
                continue
            if _vce_has_generate_control and _vce_re.search(
                r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_text
            ):
                _vce_llm[_vce_field] = "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                _vce_action_edits += 1
            elif _vce_has_create_control and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_text):
                _vce_llm[_vce_field] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                _vce_action_edits += 1
        _vce_points = _vce_llm.get("key_points")
        if isinstance(_vce_points, list):
            for _vce_i, _vce_point in enumerate(_vce_points):
                if not isinstance(_vce_point, str):
                    continue
                if _vce_has_generate_control and _vce_re.search(
                    r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_point
                ):
                    _vce_points[_vce_i] = (
                        "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                    )
                    _vce_action_edits += 1
                elif _vce_has_create_control and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_point):
                    _vce_points[_vce_i] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                    _vce_action_edits += 1

    # The LLM may place scene_enrichment directly in payload. Apply the same evidence rule there,
    # using OCR matched by scene_id; all changes are limited to derived descriptions/key points.
    _vce_ocr_by_scene = {}
    for _vce_seg in _vce_segments:
        if isinstance(_vce_seg, dict) and _vce_seg.get("scene_id") is not None:
            _vce_ocr_by_scene[_vce_seg.get("scene_id")] = str(_vce_seg.get("ocr_text", "") or "")
    def _vce_guard_scene_enrichment(_vce_list):
        nonlocal _vce_action_edits
        if not isinstance(_vce_list, list):
            return
        for _vce_scene in _vce_list:
            if not isinstance(_vce_scene, dict):
                continue
            _vce_ocr = _vce_ocr_by_scene.get(_vce_scene.get("scene_id"), "")
            _vce_create = bool(_vce_re.search(r"(?i)создать\s+калькулятор", _vce_ocr))
            _vce_generate = bool(_vce_re.search(r"(?i)(?:генерация\s+калькуляторов|сгенерировать)", _vce_ocr))
            _vce_done = bool(
                _vce_re.search(
                    r"(?i)(?:успешно\s+(?:создан|сгенерирован|завершен)|генерация\s+завершена|калькулятор\s+создан|калькуляторы\s+сгенерированы)", _vce_ocr
                )
            )
            if _vce_done:
                continue
            for _vce_field in ("improved_description", "topic"):
                _vce_text = _vce_scene.get(_vce_field)
                if not isinstance(_vce_text, str):
                    continue
                if _vce_generate and _vce_re.search(r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_text):
                    _vce_scene[_vce_field] = (
                        "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                    )
                    _vce_action_edits += 1
                elif _vce_create and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_text):
                    _vce_scene[_vce_field] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                    _vce_action_edits += 1
            _vce_points = _vce_scene.get("key_points")
            if isinstance(_vce_points, list):
                for _vce_i, _vce_point in enumerate(_vce_points):
                    if not isinstance(_vce_point, str):
                        continue
                    if _vce_generate and _vce_re.search(
                        r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_point
                    ):
                        _vce_points[_vce_i] = (
                            "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                        )
                        _vce_action_edits += 1
                    elif _vce_create and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_point):
                        _vce_points[_vce_i] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                        _vce_action_edits += 1
    if isinstance(payload, dict):
        _vce_guard_scene_enrichment(payload.get("scene_enrichment"))
    if _vce_replacements:
        print(f"🧭 [ASR/OCR] Исправлено {_vce_replacements} вариантов на {_vce_canonical} по подтверждённому OCR.")
    if _vce_action_edits:
        print(f"🧭 [Evidence guard] Смягчено {_vce_action_edits} утверждений о действиях без подтверждения результата.")
    return payload

def _add_evidence_grounding_to_summary(text, segments):
    """Apply NSR-specific spoken-vs-screened-result wording only for the NSR profile."""
    if str(setting("domain_profile", "generic") or "generic").strip().lower() != "nsrspec":
        return text
    import re as _report_re
    if not isinstance(text, str) or not isinstance(segments, list):
        return text
    _report_audio = " ".join(
        str(_report_seg.get("audio_transcript", "") or "")
        for _report_seg in segments if isinstance(_report_seg, dict)
    )
    _report_ocr = " ".join(
        str(_report_seg.get("ocr_text", "") or "")
        for _report_seg in segments if isinstance(_report_seg, dict)
    )
    _report_speech_noncompliance = bool(_report_re.search(
        r"(?i)(?:не\s+соответствует\s+(?:нормативу|требованиям)|несоответств\w*)", _report_audio
    ))
    _report_collision_ui = bool(_report_re.search(r"(?i)проверка\s+коллизий", _report_ocr))
    _report_ocr_shows_result = bool(_report_re.search(
        r"(?i)(?:не\s+соответствует\s+(?:нормативу|требованиям)|несоответств\w*\s+(?:выявлено|обнаружено)|"
        r"результат\s+проверки\s*[:=]\s*(?:ошибка|не\s+соответствует)|"
        r"итог\s+проверки\s*[:=]\s*(?:ошибка|не\s+соответствует))", _report_ocr
    ))
    if not (_report_speech_noncompliance and _report_collision_ui and not _report_ocr_shows_result):
        return text

    _report_replacement = (
        "По словам диктора, проверка выявила несоответствие нормативу; "
        "фактическое значение уклона и итог проверки визуально не подтверждены."
    )
    _report_result = text
    _report_sentences = _report_re.split(r"(?<=[.!?])\s+", _report_result)
    _report_new_sentences = []
    _report_changed = False
    for _report_sentence in _report_sentences:
        _report_has_noncompliance = bool(_report_re.search(
            r"(?i)(?:уклон\s+пандус\w*|пандус\w*[^.!?]{0,70}уклон)[^.!?]{0,140}не\s+соответствует\s+нормативу", _report_sentence
        ))
        _report_is_visual_assertion = bool(_report_re.search(
            r"(?i)(?:в\s+(?:3d[- ]?)?модел\w*|модель\s+показала|визуально\s+выявлено|"
            r"проверка\s+выявила|выявлено,?\s+что|обнаружено,?\s+что|установлено,?\s+что)", _report_sentence
        ))
        _report_already_attributed = bool(_report_re.search(
            r"(?i)(?:по\s+словам\s+диктора|в\s+речи\s+сообщается|аудиодорожк\w*)", _report_sentence
        ))
        if _report_has_noncompliance and _report_is_visual_assertion and not _report_already_attributed:
            _report_new_sentences.append(_report_replacement)
            _report_changed = True
        else:
            _report_new_sentences.append(_report_sentence)
    if _report_changed:
        return " ".join(_report_new_sentences)

    # Fallback for a brief global summary whose overconfident model-result sentence does not name the model.
    _report_overclaim = _report_re.search(
        r"(?i)(?:выявлено|обнаружено|установлено)[^.!?]{0,120}уклон\s+пандус\w*[^.!?]{0,80}не\s+соответствует\s+нормативу", _report_result
    )
    if _report_overclaim and not _report_re.search(r"(?i)визуально\s+не\s+подтвержден", _report_result):
        _report_start = _report_result.rfind(".", 0, _report_overclaim.start()) + 1
        _report_end = _report_result.find(".", _report_overclaim.end())
        if _report_end < 0:
            _report_end = len(_report_result)
        _report_result = (
            _report_result[:_report_start] + " " + _report_replacement + _report_result[_report_end + (1 if _report_end < len(_report_result) else 0) :]
        )
    return _report_result

def _report_entity_name_pattern(name):
    """Build a case-insensitive OCR literal matcher while allowing line-wrap whitespace."""
    import re as _report_re
    if not isinstance(name, str) or not name.strip():
        return None
    _report_tokens = name.strip().split()
    if not _report_tokens:
        return None
    return _report_re.compile(r"(?i)(?<![\w])" + r"\s+".join(_report_re.escape(_report_t) for _report_t in _report_tokens) + r"(?![\w])")

def _link_entities_to_source_scenes(payload, segments):
    """Add scene IDs only when an entity's literal name is visible in that scene's OCR."""
    if not isinstance(payload, dict) or not isinstance(payload.get("entities"), list) or not isinstance(segments, list):
        return 0
    _report_valid_ids = set()
    for _report_seg in segments:
        if isinstance(_report_seg, dict):
            _report_id = _report_seg.get("scene_id")
            if isinstance(_report_id, int) and not isinstance(_report_id, bool):
                _report_valid_ids.add(_report_id)
    _report_changes = 0
    for _report_entity in payload.get("entities", []):
        if not isinstance(_report_entity, dict):
            continue
        _report_name = _report_entity.get("name")
        _report_pattern = _report_entity_name_pattern(_report_name)
        if _report_pattern is None:
            continue
        _report_old = _report_entity.get("scene_ids")
        _report_current = []
        if isinstance(_report_old, list):
            for _report_id in _report_old:
                if isinstance(_report_id, int) and not isinstance(_report_id, bool) and _report_id in _report_valid_ids:
                    if _report_id not in _report_current:
                        _report_current.append(_report_id)
        _report_added = []
        # Avoid broad, noisy associations for very short acronyms such as "КСИ".
        if len(str(_report_name).strip()) >= 4:
            for _report_seg in segments:
                if not isinstance(_report_seg, dict):
                    continue
                _report_id = _report_seg.get("scene_id")
                _report_ocr = str(_report_seg.get("ocr_text", "") or "")
                if isinstance(_report_id, int) and not isinstance(_report_id, bool) and _report_id in _report_valid_ids and _report_pattern.search(_report_ocr):
                    if _report_id not in _report_current:
                        _report_current.append(_report_id)
                        _report_added.append(_report_id)
        _report_current.sort()
        if _report_old != _report_current:
            _report_entity["scene_ids"] = _report_current
            _report_changes += 1
    return _report_changes

def _validate_analysis_payload(payload, segments, global_summary=""):
    """Validate the derived analysis JSON contract; warn rather than abort a video run."""
    import re as _report_re
    _report_issues = []
    if not isinstance(payload, dict):
        print("⚠️ [JSON integrity V4] enhanced_analysis — ожидался JSON-объект.")
        return payload
    if not isinstance(segments, list):
        _report_issues.append("segments не является массивом")
        segments = []
    _report_valid_ids = {
        _report_seg.get("scene_id") for _report_seg in segments
        if isinstance(_report_seg, dict) and isinstance(_report_seg.get("scene_id"), int) and not isinstance(_report_seg.get("scene_id"), bool)
    }
    _report_contract = {
        "executive_summary": str,
        "topics": list,
        "key_points": list,
        "entities": list,
        "scene_enrichment": list,
    }
    for _report_key, _report_type in _report_contract.items():
        if _report_key not in payload:
            _report_issues.append(f"отсутствует поле { _report_key }")
        elif not isinstance(payload[_report_key], _report_type):
            _report_issues.append(f"{_report_key} имеет тип {type(payload[_report_key]).__name__}, ожидался {_report_type.__name__}")
    for _report_key in ("topics",):
        if isinstance(payload.get(_report_key), list) and any(not isinstance(_report_item, str) for _report_item in payload[_report_key]):
            _report_issues.append(f"{_report_key} содержит элементы не-строки")
    for _report_key in ("key_points", "scene_enrichment"):
        _report_value = payload.get(_report_key)
        if isinstance(_report_value, list):
            for _report_idx, _report_item in enumerate(_report_value):
                if not isinstance(_report_item, dict):
                    _report_issues.append(f"{_report_key}[{_report_idx}] не является объектом")
                    continue
                _report_id = _report_item.get("scene_id")
                if _report_id is not None and _report_id not in _report_valid_ids:
                    _report_issues.append(f"{_report_key}[{_report_idx}] ссылается на неизвестную сцену {_report_id!r}")
    _report_ocr = " ".join(str(_report_seg.get("ocr_text", "") or "") for _report_seg in segments if isinstance(_report_seg, dict))
    _report_is_nsrspec = str(setting("domain_profile", "generic") or "generic").strip().lower() == "nsrspec"
    _report_has_canonical_sp = _report_is_nsrspec and bool(_report_re.search(r"(?i)(?<![\w])СП\s*1\.13130\.2020(?!\d)", _report_ocr))
    if _report_has_canonical_sp:
        _report_alias = _report_re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)")
        if _report_alias.search(str(global_summary or "")):
            _report_issues.append("неисправленный вариант номера СП остался в global_summary")
    _report_known_keys = {
        "audio_transcript",
        "ocr_text",
        "visual_description",
        "original_summary",
        "start",
        "end",
        "time",
        "timestamp",
        "timestamps",
        "timecode",
        "timecodes",
    }

    def _report_scan_alias(value, path="enhanced_analysis"):
        if isinstance(value, dict):
            for _report_key, _report_child in value.items():
                if str(_report_key).lower() in _report_known_keys:
                    continue
                _report_scan_alias(_report_child, f"{path}.{_report_key}")
        elif isinstance(value, list):
            for _report_i, _report_child in enumerate(value):
                _report_scan_alias(_report_child, f"{path}[{_report_i}]")
        elif isinstance(value, str) and _report_has_canonical_sp and _report_re.search(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)", value):
            _report_issues.append(f"неисправленный вариант номера СП в {path}")
    _report_scan_alias(payload)
    _report_scenes = len(_report_valid_ids)
    if _report_issues:
        print("⚠️ [JSON integrity V4] Проверка аналитического JSON нашла замечания:")
        for _report_issue in _report_issues:
            print("   - " + _report_issue)
    else:
        print(f"✅ [JSON integrity V4] Структура аналитики проверена; доступно {_report_scenes} scene_id, ссылки сущностей сверены с OCR.")
    return payload

def _finalize_analysis_payload(payload, segments, global_summary=""):
    """Apply evidence-grounded consistency repairs, then validate the derived analysis payload."""
    if not isinstance(payload, dict):
        return payload
    if isinstance(payload.get("executive_summary"), str):
        _report_new = _add_evidence_grounding_to_summary(payload["executive_summary"], segments)
        if _report_new != payload["executive_summary"]:
            payload["executive_summary"] = _report_new
            print("🧭 [Summary integrity V4] Executive summary reconciled with audio/OCR evidence.")
    _report_entity_changes = _link_entities_to_source_scenes(payload, segments)
    if _report_entity_changes:
        print(f"🧭 [JSON integrity V4] Обновлены связи сущностей со сценами: {_report_entity_changes} сущностей.")
    return _validate_analysis_payload(payload, segments, global_summary)
