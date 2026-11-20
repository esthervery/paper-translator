"""논문 요약 (OpenAI Responses API로 PDF 원문 직접 전달).

- 모델: config.json의 summary_model (기본 gpt-6-luna)
- PDF는 base64 data URL로 input_file 파트에 담아 전송한다(레이아웃·수식·그림까지
  모델이 직접 본다). PDF 직접 입력은 30페이지/30MB 한도가 있어 초과 시 앞 30쪽만
  추출해 임시 파일로 보낸다.
- 프롬프트는 앱 폴더의 "논문 번역 프롬프트.txt"가 있으면 그것을, 없으면 내장
  기본값(사용자가 제공한 프롬프트와 동일 내용)을 쓴다.
"""
import base64
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

import config

PROMPT_FILENAME = "논문 번역 프롬프트.txt"
PDF_MAX_PAGES = 30
PDF_MAX_BYTES = 30 * 1024 * 1024

DEFAULT_PROMPT = """A. 1단계(PDF를 처음 받았을 때) 출력 형식 — 고정
이 단계는 '나'가 논문을 본격적으로 읽기 직전 준비 단계이므로, 너는 다음의 네 가지만 출력.

1. 논문 한 줄 요약
2. 문제의식 (배경과 동기)
3. 논문 읽기 전 미리 알아두면 좋을 용어
4. 읽을 때 주의할 점

B. 출력하지 않는 것 — 문제 정식화, 방법론, 실험 결과 요약 등에 대해, 꺼내지 않음.

C. 용어 표기 규칙 (갱신)
기본 형식: 원어 앞, 한글 괄호 — 예: Differential Privacy(차분 프라이버시)
- 어려운 한자어·학술 번역투는 쓰지 않고 쉬운 말로 풀어씁니다.
예시1: 외삽하다 → "궤적을 바깥으로 늘려서 추정하다"
예시2: 가역적이다 → "되돌릴 수 있다"
+ 관용 표기 교정: catastrophic forgetting → 재앙적 망각, machine unlearning → 머신 언러닝 (또는 기계 망각)

D. 그 외 반복 용어는 첫 등장 시에만 병기, 이후 원어로 통일. 한국어 번역어가 겹쳐 헷갈리는 용어(regularization/normalization 등)는 짧은 메모 추가.

위 규칙에 따라 이 PDF 논문의 '읽기 전 준비 요약'을 한국어로 작성하세요."""


def load_prompt_text():
    p = Path(config.APP_DIR) / PROMPT_FILENAME
    if p.exists():
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            pass
    return DEFAULT_PROMPT


def _prepare_pdf_bytes(pdf_path):
    """30쪽/30MB 한도에 맞춘 PDF 바이트를 반환. (초과 시 앞 30쪽만)"""
    import fitz

    doc = fitz.open(str(pdf_path))
    too_many = doc.page_count > PDF_MAX_PAGES
    if not too_many:
        raw = Path(pdf_path).read_bytes()
        if len(raw) <= PDF_MAX_BYTES:
            doc.close()
            return raw, False
        doc.close()
        return Path(pdf_path).read_bytes()[:0], True  # 크기 초과(드묾) — 아래 재시도
    trimmed = fitz.open()
    trimmed.insert_pdf(doc, from_page=0, to_page=PDF_MAX_PAGES - 1)
    tmp = Path(tempfile.gettempdir()) / f"pt_summary_{datetime.now().timestamp():.0f}.pdf"
    trimmed.save(str(tmp))
    trimmed.close()
    doc.close()
    return tmp.read_bytes(), True


def summarize_pdf(pdf_path, model, api_key, client_factory=None):
    """PDF 원문을 모델에 직접 보내 '읽기 전 준비 요약'을 받아온다."""
    from openai import OpenAI

    make = client_factory or (lambda: OpenAI(api_key=api_key))
    client = make()

    data, was_trimmed = _prepare_pdf_bytes(pdf_path)
    b64 = base64.b64encode(data).decode("ascii")
    prompt = load_prompt_text()
    note = ("\n\n(참고: 원본이 30페이지를 초과해 앞 30페이지 기준으로 요약했습니다.)"
            if was_trimmed else "")

    resp = client.responses.create(
        model=model,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt + note},
                {"type": "input_file",
                 "filename": Path(pdf_path).name,
                 "file_data": f"data:application/pdf;base64,{b64}"},
            ],
        }],
    )
    return resp.output_text


class SummaryWorker(QThread):
    """UI를 막지 않고 요약을 생성하는 워커 스레드."""

    ok = Signal(str)
    error = Signal(str)

    def __init__(self, pdf_path, model, api_key, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.model = model
        self.api_key = api_key

    def run(self):
        try:
            self.ok.emit(summarize_pdf(self.pdf_path, self.model, self.api_key))
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")