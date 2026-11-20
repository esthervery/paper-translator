"""번역 실패 원인 진단. 이 파일을 앱 폴더(app.py와 같은 곳)에 두고 실행:

    conda activate papertrans
    cd /d "앱 폴더"
    python diag.py

앱이 실제로 쓰는 것과 동일한 형태(JSON 모드 + temperature + 배치 페이로드)로
호출을 재현해서, 어느 단계가 깨지는지 한 단계씩 출력한다.
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402


def main():
    folder = Path(__file__).resolve().parent
    print("폴더:", folder)

    env = folder / ".env"
    if env.exists():
        print(f"1) .env 있음 ({env.stat().st_size}바이트) — 파일명이 '.env.txt'가 아닌지 확인 완료")
    else:
        print("1) .env 없음 — 환경변수 OPENAI_API_KEY 또는 app.py 옆 .env 필요")

    key = config.load_api_key()
    if not key:
        print("2) 키 로드: 실패 — 앱도 같은 이유로 원문만 표시합니다. .env 위치/파일명/환경변수 확인")
        return
    print(f"2) 키 로드: OK ({key[:7]}...)")

    cfg = config.load_config()
    model = cfg["model"]
    print(f"3) 모델: {model}")

    from openai import OpenAI
    c = OpenAI(api_key=key)

    try:
        r = c.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "Say OK"}])
        print("4) 일반 호출: OK ->", r.choices[0].message.content.strip()[:40])
    except Exception as e:
        print("4) 일반 호출 실패:", type(e).__name__, e)
        return

    try:
        payload = {"segments": {
            "0": "Differential privacy is a formal framework.",
            "1": "The parameter epsilon controls the privacy-accuracy tradeoff.",
        }}
        r = c.chat.completions.create(
            model=model,
            temperature=cfg["temperature"],
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content":
                 'Translate each segment to Korean. Return a STRICT JSON object '
                 '{"<segment id>": "<Korean translation>"} containing EVERY id.'},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        raw = r.choices[0].message.content
        d = json.loads(raw)
        ok = isinstance(d, dict) and "0" in d and "1" in d
        if ok:
            print("5) 앱과 동일한 형태 호출(JSON 모드): OK")
            print("   번역 결과 예:", str(d)[:120])
        else:
            print("5) 앱과 동일한 형태 호출: 응답은 왔지만 형식 불일치!")
            print("   응답 원문:", (raw or "")[:200])
            print("   -> 이것이 '번역 대기/실패'의 원인입니다. config.json의 model을 바꿔보세요.")
    except Exception as e:
        print("5) 앱과 동일한 형태 호출 실패:", type(e).__name__, e)
        print("   -> 앱의 실패 원인입니다. 메시지를 그대로 공유해 주세요.")
        traceback.print_exc(limit=2)

    print("\n진단 끝. 4~5번 중 실패한 단계의 메시지가 원인입니다.")


if __name__ == "__main__":
    main()
