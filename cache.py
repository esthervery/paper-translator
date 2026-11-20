"""SQLite 캐시.

설계: 번역 결과는 '문장 원문의 해시'를 키로 저장한다.
- 같은 PDF를 다시 열면 -> 모든 문장이 캐시 히트 -> API 호출 0건
- 개정판(v1 -> v2)처럼 일부만 바뀐 PDF는 -> 바뀐 문장만 번역
- 레이아웃(문장 좌표)은 파일 해시별로 별도 저장
"""
import hashlib
import json
import sqlite3
import time


class Cache:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS translations(
                src_hash TEXT PRIMARY KEY,
                src_text TEXT,
                dst_text TEXT,
                model TEXT,
                created_at REAL
            );
            CREATE TABLE IF NOT EXISTS layouts(
                file_hash TEXT PRIMARY KEY,
                n_segments INTEGER,
                updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS notes(
                file_hash TEXT,
                note_id INTEGER,
                page INTEGER,
                x_frac REAL,
                y_frac REAL,
                text TEXT,
                PRIMARY KEY(file_hash, note_id)
            );
            CREATE TABLE IF NOT EXISTS strokes(
                file_hash TEXT,
                stroke_id INTEGER,
                page INTEGER,
                color TEXT,
                points TEXT,
                PRIMARY KEY(file_hash, stroke_id)
            );
            CREATE TABLE IF NOT EXISTS bookmarks(
                file_hash TEXT,
                page INTEGER,
                PRIMARY KEY(file_hash, page)
            );
            CREATE TABLE IF NOT EXISTS segments(
                file_hash TEXT,
                sent_id INTEGER,
                page INTEGER,
                bbox TEXT,
                src_hash TEXT,
                src_text TEXT,
                PRIMARY KEY(file_hash, sent_id)
            );
            """
        )
        self.conn.commit()

    @staticmethod
    def hash_text(s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def hash_file(path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # --- 번역 결과 ---
    def get_translation(self, src_hash: str):
        row = self.conn.execute(
            "SELECT dst_text FROM translations WHERE src_hash=?", (src_hash,)
        ).fetchone()
        return row[0] if row else None

    def put_translations(self, items, model: str):
        """items: [(src_hash, src_text, dst_text), ...]"""
        self.conn.executemany(
            "INSERT OR REPLACE INTO translations(src_hash,src_text,dst_text,model,created_at)"
            " VALUES(?,?,?,?,?)",
            [(h, s, d, model, time.time()) for h, s, d in items],
        )
        self.conn.commit()

    # --- 레이아웃(문장 좌표) ---
    def save_layout(self, file_hash: str, segments):
        c = self.conn
        c.execute("DELETE FROM segments WHERE file_hash=?", (file_hash,))
        c.executemany(
            "INSERT OR REPLACE INTO segments(file_hash,sent_id,page,bbox,src_hash,src_text)"
            " VALUES(?,?,?,?,?,?)",
            [
                (file_hash, s["id"], s["page"], json.dumps(s["bbox"]), s["src_hash"], s["text"])
                for s in segments
            ],
        )
        c.execute(
            "INSERT OR REPLACE INTO layouts(file_hash,n_segments,updated_at) VALUES(?,?,?)",
            (file_hash, len(segments), time.time()),
        )
        c.commit()

    # --- 메모지 ---
    def get_notes(self, fh):
        return [
            {"note_id": r[0], "page": r[1], "x_frac": r[2], "y_frac": r[3], "text": r[4]}
            for r in self.conn.execute(
                "SELECT note_id,page,x_frac,y_frac,text FROM notes WHERE file_hash=?"
                " ORDER BY note_id", (fh,))
        ]

    def put_note(self, fh, note_id, page, x_frac, y_frac, text):
        self.conn.execute(
            "INSERT OR REPLACE INTO notes(file_hash,note_id,page,x_frac,y_frac,text)"
            " VALUES(?,?,?,?,?,?)", (fh, note_id, page, x_frac, y_frac, text))
        self.conn.commit()

    def delete_note(self, fh, note_id):
        self.conn.execute("DELETE FROM notes WHERE file_hash=? AND note_id=?", (fh, note_id))
        self.conn.commit()

    # --- 형광펜 ---
    def get_strokes(self, fh):
        import json as _json
        out = []
        for r in self.conn.execute(
                "SELECT stroke_id,page,color,points FROM strokes WHERE file_hash=?"
                " ORDER BY stroke_id", (fh,)):
            out.append({"stroke_id": r[0], "page": r[1], "color": r[2],
                        "points": _json.loads(r[3])})
        return out

    def put_stroke(self, fh, stroke_id, page, color, points):
        import json as _json
        self.conn.execute(
            "INSERT OR REPLACE INTO strokes(file_hash,stroke_id,page,color,points)"
            " VALUES(?,?,?,?,?)",
            (fh, stroke_id, page, color, _json.dumps([[float(a), float(b)] for a, b in points])))
        self.conn.commit()

    def delete_stroke(self, fh, stroke_id):
        self.conn.execute("DELETE FROM strokes WHERE file_hash=? AND stroke_id=?",
                          (fh, stroke_id))
        self.conn.commit()

    # --- 중요 페이지 ---
    def get_bookmarks(self, fh):
        return sorted(r[0] for r in self.conn.execute(
            "SELECT page FROM bookmarks WHERE file_hash=?", (fh,)))

    def set_bookmark(self, fh, page, on):
        if on:
            self.conn.execute(
                "INSERT OR REPLACE INTO bookmarks(file_hash,page) VALUES(?,?)", (fh, page))
        else:
            self.conn.execute("DELETE FROM bookmarks WHERE file_hash=? AND page=?", (fh, page))
        self.conn.commit()

    def segment_count(self, file_hash: str) -> int:
        row = self.conn.execute(
            "SELECT n_segments FROM layouts WHERE file_hash=?", (file_hash,)
        ).fetchone()
        return row[0] if row else 0
