# 논문 번역 뷰어 (로컬 전용)

영어 논문 PDF를 왼쪽에, **문장 단위 한글 번역**을 오른쪽에 나란히 보여주는 데스크톱 프로그램입니다.

- 오른쪽 문장에 **마우스를 올리면** 왼쪽 원문 PDF의 해당 문장에 노란 **하이라이트 + 자동 스크롤**, **클릭하면 고정(핀)** — hover 박스와 함께 최대 2개 표시
- 오른쪽 번역은 **페이지 단위로 구분**되고, 텍스트가 끝까지 표시(말줄임 없음), **드래그 선택 복사** 가능. 번역 카드를 **우클릭 → 자세히 보기**하면 한글 번역과 영문 원문을 위아래로 나란히 비교·복사할 수 있습니다
- **논문 요약**: PDF를 열면 gpt-6-luna가 PDF 원문을 직접 읽고 '읽기 전 준비 요약'을 자동 생성 — 툴바 '요약' 버튼으로 확인 (프롬프트는 앱 폴더의 `논문 번역 프롬프트.txt`에서 수정 가능)
- **메모**: 여백(아이보리)을 클릭하면 포스트잇이 생기고 그 자리에서 작성 — 확대/축소해도 위치·크기 비율 유지
- **형광펜** 4색(노랑/초록/분홍/파랑) + 지우개, **중요 페이지** 별표 + 빠른 이동
- 번역·메모·형광펜·요약 모두 `translations.db`에 영구 저장 — 같은 PDF를 다시 열면 API 호출 0건

## 요구 사항

- Windows + Miniconda
- OpenAI API 키 (본인 PC에만 저장하세요 — 채팅이나 코드에 붙여넣지 않습니다)

## 설치 (최초 1회 — 수동, 권장)

"Anaconda Prompt (miniconda3)" 또는 conda가 잡힌 터미널에서:

```bat
conda create -n papertrans python=3.11 -y
conda activate papertrans
cd /d "이 폴더 경로 (예: D:\tools\paper-translator)"
pip install -r requirements.txt
```

(PowerShell이면 `cd /d ...` 대신 `cd "...") 

같은 폴더에 **`.env`** 파일을 새로 만들어(메모장 저장 시 확장자 `.txt`가 안 붙도록 주의) 한 줄 넣습니다:
```
OPENAI_API_KEY=sk-본인키
```
(또는 Windows 시스템 환경변수 `OPENAI_API_KEY`를 설정해도 됨)

> 참고: `setup.bat` / `run.bat`은 conda가 **시스템 PATH에 잡혀 있을 때만** 동작한다.
> Miniconda 기본 설치는 PATH에 conda를 넣지 않으므로(설치기 권장 옵션) 더블클릭으로는
> "conda not found"로 끝난다. 위 수동 방식이 정석이고, 배치 파일 두 개는 없어도 된다.

## 실행

```bat
conda activate papertrans
cd /d "이 폴더 경로"
python app.py
```

- PDF를 인자로 바로 열기: `python app.py "C:\papers\논문.pdf"`
- 매번 `conda activate`가 귀찮으면 Anaconda Prompt 대신 바로가기로
  `%USERPROFILE%\miniconda3\envs\papertrans\python.exe app.py`를 작업 폴더 지정해 만들어도 됨

## 번역 저장 — 영구 보관

- 번역 결과는 앱 폴더의 **`translations.db`**(SQLite 파일)에 **영구 저장**됩니다. 만료나 자동 삭제가 없고, 프로그램을 다시 실행해도 남습니다.
- 성공한 문장만 저장됩니다(실패분은 저장 안 됨 → 다음 실행 때 그 문장만 다시 시도).
- 같은 PDF를 다시 열면 저장된 번역을 그대로 표시 → **API 호출 0건**
- 개정판(v1→v2)처럼 일부만 바뀐 PDF → **바뀐 문장만** 새로 번역 (문장 단위 매칭이라 가능)
- **백업/이전**: `translations.db` 파일 하나만 복사하면 됩니다 (다른 PC·다른 폴더에서도 동일하게 복원)
- 이 파일을 삭제하면 그동안의 번역도 사라집니다 (원본 PDF는 영향 없음) — 지우지 마세요
- 10쪽 논문 1편 번역 비용은 gpt-4o-mini 기준 대략 10~30원 수준

## 설정 (`config.json`, 없으면 기본값 사용)

| 키 | 기본값 | 설명 |
|---|---|---|
| `model` | `gpt-4o-mini` | 번역 모델. 문자열만 교체하면 됨 |
| `batch_max_chars` | `3500` | API 요청 1건에 담을 글자 수 상한 |
| `batch_max_items` | `40` | API 요청 1건에 담을 문장 수 상한 |
| `pdf_zoom` | `1.5` | PDF 렌더 배율 |

## 툴바

`PDF 열기` · `실패분 다시 번역`(일부 문장이 실패했을 때만 표시) · `확대/축소` · `글자 크기`

## 한계

- 스캔본(텍스트 레이어가 없는 PDF)은 미지원 — OCR은 이후 단계에서 추가 가능
- 수식 조각·그림 내부 텍스트는 자동 제외되거나 번역 품질이 떨질 수 있음
- 특이 레이아웃(3단 등)에서는 읽기 순서가 어긋날 수 있음 (일반 1·2단 논문은 정상)

## 파일 구조

| 파일 | 역할 |
|---|---|
| `app.py` | 메인 윈도우, 동기화 로직 |
| `pdf_view.py` | PDF 렌더링 + 하이라이트 오버레이 + 클릭 역동기화 |
| `translation_panel.py` | 오른쪽 번역 패널 (hover 시그널) |
| `extractor.py` | 문장 분할 + 좌표(bbox) 추출 |
| `translator.py` | OpenAI 배치 번역 (+ 워커 스레드) |
| `cache.py` | 번역 저장 DB (translations.db, 영구) |
| `config.py` | 설정, API 키 로드 |
| `summarizer.py` | 논문 요약 (Responses API로 PDF 원문 직접 전달) |
| `논문 번역 프롬프트.txt` | 요약 프롬프트 (없으면 내장 기본값 사용) |