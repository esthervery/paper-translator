"""PDF에서 '문장 단위 세그먼트'(원문 텍스트 + 페이지 + 좌표 bbox)를 추출한다.

- PyMuPDF의 블록 순서는 LaTeX(pdflatex) PDF에서 대체로 올바른 읽기 순서다.
  (2단 논문도 왼쪽 단 -> 오른쪽 단 순서로 작성됨)
- 줄 합침 시 행끝 하이픈("val- ues" -> "values")을 복원한다.
- 문장 분할은 약어(Fig., Eq., et al., i.e. ...)와 이름 이니셜 뒤의 마침표는
  분할점으로 쓰지 않도록 가드한다.
- 수식 조각/워터마크처럼 알파벳 비율이 낮은 조각은 번역 대상에서 제외한다.
"""
import hashlib
import re

try:
    import pymupdf as fitz   # PyMuPDF 공식 이름 (구 이름 fitz는 폐기 예정)
except ImportError:          # 구버전 PyMuPDF 호환
    import fitz

# 문장 분할 가드용 약어 목록 (마침표 뒤 대문자/숫자가 와도 분할하지 않음)
ABBREVS = {
    "e.g", "i.e", "et al", "al", "cf", "Cf", "resp", "Resp", "vs", "approx", "w.r.t",
    "Fig", "Figs", "fig", "Eq", "Eqs", "eq", "Sec", "Secs", "Tab", "No", "Ref", "Refs",
    "pp", "vol", "Ed", "Dr", "Prof", "Mr", "Ms", "Mrs", "Jr", "Sr", "St", "Mt",
    "Capt", "Col", "Gen", "Rep", "Sen", "Inc", "Ltd", "U.S", "U.K",
}
_PUNCT = re.compile(r'[.!?]+["\u2019\u201d)\]]*(?=\s+)')
_NEXT_OK = re.compile(r"^[A-Z0-9(\[\"'\u201c\u2018]")
_WORD_BEFORE = re.compile(r"([A-Za-z][A-Za-z.]*)$")
_NOISE_LINE = (
    re.compile(r"arXiv:\d{4}\.\d{4,5}"),  # arXiv 워터마크
)


def _split_spans(text: str):
    """문장 (start, end) 범위 목록을 반환."""
    spans, start = [], 0
    for m in _PUNCT.finditer(text):
        nxt = text[m.end():].lstrip()[:1]
        if not nxt or not _NEXT_OK.match(nxt):
            continue
        w = _WORD_BEFORE.search(text[: m.start()])
        if w:
            token = w.group(1)
            if token in ABBREVS:
                continue
            if len(token) == 1 and token.isupper():  # 이름 이니셜 "J. Kim"
                continue
        spans.append((start, m.end()))
        start = m.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def _keep_sentence(s: str) -> bool:
    s = s.strip()
    if len(s) < 6:
        return False
    letters = sum(1 for ch in s if ch.isascii() and ch.isalpha())
    return letters >= 6 and letters / len(s) >= 0.35


def extract_segments(pdf_path) -> list:
    """반환: [{"id","page","bbox":[x0,y0,x1,y1],"text","src_hash"}, ...] (읽기 순서)"""
    doc = fitz.open(str(pdf_path))
    segs = []
    for pno, page in enumerate(doc):
        d = page.get_text("dict")
        for block in d["blocks"]:
            if block.get("type") != 0:
                continue
            lines, sizes = [], []
            for ln in block["lines"]:
                if abs(ln["dir"][0]) < 0.95:  # 세로쓰기(측면 워터마크 등) 제외
                    continue
                t = "".join(sp["text"] for sp in ln["spans"]).strip()
                if not t:
                    continue
                sz = [sp["size"] for sp in ln["spans"] if sp["text"].strip()]
                sizes.append(sum(sz) / len(sz))
                lines.append((t, fitz.Rect(ln["bbox"])))
            if not lines or sum(sizes) / len(sizes) < 4.5:
                continue
            buf, recs = "", []  # recs: (char_start, char_end, line_bbox)
            for t, r in lines:
                if any(p.search(t) for p in _NOISE_LINE):
                    continue
                if re.fullmatch(r"\d{1,4}", t):  # 페이지 번호
                    continue
                if buf.endswith("-") and (t[:1].islower() or t[:1].isdigit()):
                    buf = buf[:-1] + t          # 행끝 하이픈 복원
                else:
                    buf = (buf + " " + t) if buf else t
                recs.append((len(buf) - len(t), len(buf), r))
            for a, b in _split_spans(buf):
                text = buf[a:b].strip()
                if not _keep_sentence(text):
                    continue
                boxes = [r for (ra, rb, r) in recs if ra < b and rb > a]
                if not boxes:
                    continue
                u = boxes[0]
                for r in boxes[1:]:
                    u |= r
                segs.append(
                    {"page": pno, "bbox": [u.x0, u.y0, u.x1, u.y1], "text": text}
                )
    doc.close()

    out = []
    for i, s in enumerate(segs):
        s["id"] = i
        s["src_hash"] = hashlib.sha256(s["text"].encode("utf-8")).hexdigest()[:16]
        out.append(s)
    return out