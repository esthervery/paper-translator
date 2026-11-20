"""OpenAI API 배치 번역.

토큰 절약 전략:
- 문장 N개를 JSON 객체로 묶어 요청 1건에 처리 (응답도 JSON 강제)
- 결과는 캐시(cache.py)에 문장 해시 단위로 저장 -> 재열면 호출 0건

진단 지원:
- 모든 시도/실패를 앱 폴더의 app.log에 기록 (실패 원인 추적용)
- 모델이 요청한 id 형태로 응답하지 않으면(형식 불일치) 재시도 후 오류로 기록
"""
import json
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

import config

LOG_PATH = Path(config.APP_DIR) / "app.log"

SYSTEM_PROMPT = """You are a professional translator for academic papers (English -> Korean).
Translate every segment into natural and accurate Korean.
Rules:
- Keep technical terminology consistent; when the Korean term is not widely
  established, put the English term in parentheses.
- Keep inline math, variable names, citations like [12], and proper nouns intact.
- Do not add explanations or notes. Translate only.
Return a STRICT JSON object: {"<segment id>": "<Korean translation>", ...}
containing EVERY id given in the input. No extra keys, no comments."""


def _log(msg: str):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _batches(segments, max_chars, max_items):
    batch, chars = [], 0
    for s in segments:
        n = max(len(s["text"]), 8)
        if batch and (chars + n > max_chars or len(batch) >= max_items):
            yield batch
            batch, chars = [], 0
        batch.append(s)
        chars += n
    if batch:
        yield batch


def translate_segments(segments, model, api_key, progress_cb=None,
                       client_factory=None, max_retries=2, partial_cb=None):
    """모든 세그먼트 번역을 시도하고 {src_hash: dst_text}를 반환.

    일부 배치가 실패하면 그 문장들은 결과에서 누락된다(재시도 가능).
    전부 실패했으면 마지막 오류를 RuntimeError로 올린다.
    client_factory: 테스트용 주입점.
    """
    from openai import OpenAI

    _log(f"=== 번역 시작: {len(segments)}문장, model={model}, "
         f"key={(api_key or '')[:7]}... ===")
    make = client_factory or (lambda: OpenAI(api_key=api_key))
    client = make()
    cfg = config.load_config()
    batches = list(_batches(segments, cfg["batch_max_chars"], cfg["batch_max_items"]))
    total = sum(len(b) for b in batches)
    result, done, last_err = {}, 0, None

    for bi, batch in enumerate(batches):
        payload = {str(s["id"]): s["text"] for s in batch}
        matched = None
        for attempt in range(max_retries + 1):
            try:
                kw = {"model": model, "response_format": {"type": "json_object"}}
                if cfg.get("temperature") is not None:
                    kw["temperature"] = cfg["temperature"]   # null이면 API 기본값
                resp = client.chat.completions.create(
                    **kw,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",
                         "content": json.dumps({"segments": payload}, ensure_ascii=False)},
                    ],
                )
                raw = resp.choices[0].message.content
                d = json.loads(raw)
                if not isinstance(d, dict):
                    last_err = f"응답이 JSON 객체가 아님: {type(d).__name__}"
                    _log(f"배치{bi + 1} 시도{attempt + 1}: {last_err}")
                    time.sleep(1.2 * (attempt + 1))
                    continue
                got = {}
                for s in batch:
                    v = d.get(str(s["id"]))
                    if isinstance(v, str) and v.strip():
                        got[s["src_hash"]] = v.strip()
                if not got:
                    last_err = "응답 JSON에 세그먼트 id가 없음 (형식 불일치)"
                    _log(f"배치{bi + 1} 시도{attempt + 1}: 형식 불일치, 원문: {(raw or '')[:300]}")
                    time.sleep(1.2 * (attempt + 1))
                    continue
                matched = got
                _log(f"배치{bi + 1} 시도{attempt + 1}: 성공 {len(got)}/{len(batch)}문장")
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                _log(f"배치{bi + 1} 시도{attempt + 1}: 실패 — {last_err}")
                time.sleep(1.2 * (attempt + 1))
        if matched:
            result.update(matched)
            if partial_cb:
                partial_cb(matched)   # 배치 단위 실시간 반영
        done += len(batch)
        if progress_cb:
            progress_cb(done, total)

    if not result and segments and last_err:
        _log(f"=== 전체 실패: {last_err} ===")
        raise RuntimeError(f"{last_err}\n\n(상세 기록: app.log)")
    _log(f"=== 번역 종료: 성공 {len(result)}/{total}문장 ===")
    return result


class TranslationWorker(QThread):
    """UI를 막지 않고 번역을 실행하는 워커 스레드."""

    progress = Signal(int, int)   # (완료 문장 수, 전체 문장 수)
    partial = Signal(dict)        # 배치 1건이 끝날 때마다 {src_hash: dst_text}
    ok = Signal(dict)             # {src_hash: dst_text}
    error = Signal(str)

    def __init__(self, segments, model, api_key, parent=None):
        super().__init__(parent)
        self.segments = segments
        self.model = model
        self.api_key = api_key

    def run(self):
        try:
            res = translate_segments(
                self.segments, self.model, self.api_key,
                progress_cb=lambda d, t: self.progress.emit(d, t),
                partial_cb=lambda m: self.partial.emit(m),
            )
            if not res and self.segments:
                self.error.emit(
                    "번역 결과가 비어 있습니다. 모델명(config.json)·API 키·네트워크를 확인하세요.\n"
                    f"(상세 기록: {LOG_PATH})"
                )
            else:
                self.ok.emit(res)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")