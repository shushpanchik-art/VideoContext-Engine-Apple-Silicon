#!/usr/bin/env python3
"""P0 smoke test: verify ffmpeg_ocr_workers can be read, saved and read back via VCE API.

This intentionally posts only the current worker value, so it tests the setting
without changing the user's selected value or overwriting other preferences.
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, object]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=8) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"raw": raw}
            return response.status, parsed
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, parsed
    except URLError as exc:
        raise RuntimeError(f"Не удалось подключиться к VCE API: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:7555")
    args = parser.parse_args()
    endpoint = args.base_url.rstrip("/") + "/api/v1/ui-settings"

    try:
        status, settings = request_json(endpoint)
        if status != 200:
            raise RuntimeError(f"GET settings вернул HTTP {status}: {settings}")
        if not isinstance(settings, dict):
            raise RuntimeError(f"GET settings вернул не JSON-объект: {settings!r}")

        workers = settings.get("ffmpeg_ocr_workers")
        if type(workers) is not int or not 1 <= workers <= 8:
            raise RuntimeError(
                "GET не вернул допустимый ffmpeg_ocr_workers (int от 1 до 8): "
                f"{workers!r}. Проверь hotfix и перезапусти сервер."
            )
        print(f"PASS GET: ffmpeg_ocr_workers={workers}")

        status, response = request_json(
            endpoint, method="POST", payload={"ffmpeg_ocr_workers": workers}
        )
        if status != 200:
            raise RuntimeError(f"POST settings вернул HTTP {status}: {response}")
        print(f"PASS POST: сохранение существующего значения прошло (HTTP {status})")

        status, reloaded = request_json(endpoint)
        if status != 200 or not isinstance(reloaded, dict):
            raise RuntimeError(f"Повторный GET завершился HTTP {status}: {reloaded}")
        if reloaded.get("ffmpeg_ocr_workers") != workers:
            raise RuntimeError(
                f"Значение изменилось после сохранения: было {workers}, стало "
                f"{reloaded.get('ffmpeg_ocr_workers')!r}"
            )
        print(f"PASS ROUND-TRIP: повторный GET вернул ffmpeg_ocr_workers={workers}")
        print("P0 settings smoke-test: PASSED")
        return 0
    except Exception as exc:
        print(f"P0 settings smoke-test: FAILED — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
