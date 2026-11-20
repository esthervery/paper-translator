"""설정 로드/저장 및 OpenAI API 키 확인.

API 키는 다음 우선순위로 읽는다:
  1) 환경변수 OPENAI_API_KEY
  2) 앱 폴더의 .env 파일 (OPENAI_API_KEY=sk-...)
키를 코드에 박아넣지 않는다. 절대로.
"""
import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
ENV_PATH = APP_DIR / ".env"

DEFAULTS = {
    "model": "gpt-6-luna",    # config.json에서 자유롭게 교체 가능
    "temperature": None,      # None = API 기본값 사용
    "batch_max_chars": 3500,  # 한 번의 API 요청에 담을 문장 글자수 상한
    "batch_max_items": 40,    # 한 번의 API 요청에 담을 문장 개수 상한
    "pdf_zoom": 1.5,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config_overrides(**kw):
    cfg = load_config()
    cfg.update(kw)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENAI_API_KEY"):
                _, _, v = line.partition("=")
                v = v.strip().strip('"').strip("'")
                if v:
                    return v
    return ""
