"""PDF 논문 한글 번역 뷰어 (로컬 전용).

왼쪽: PDF 원문(여백 아이보리) / 오른쪽: 페이지 구분된 한글 번역 카드.
- 우측 문장 hover = 원문 임시 하이라이트, 클릭 = 고정(핀) — 동시 최대 2개
- 여백 클릭하면 포스트잇 메모가 바로 생기고 그 자리에서 작성 (PDF 좌표 저장,
  확대/축소에 위치·크기 비율 유지) / 형광펜 4색 / 중요 페이지 별표
- PDF를 열면 요약(gpt-6-luna, PDF 원문 직접 전달)이 자동 생성 — 툴바 '요약'으로 확인
- 번역·메모·형광펜·요약 모두 translations.db에 영구 저장

실행: python app.py [논문.pdf]
API 키: 환경변수 OPENAI_API_KEY 또는 앱 폴더의 .env 파일
"""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog,
                               QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QPushButton,
                               QSplitter, QTextBrowser, QVBoxLayout)

import cache as cache_mod
import config
import detail_view
import extractor
import summarizer
import translator
from pdf_view import PdfView
from translation_panel import TranslationPanel

APP_TITLE = "논문 번역 뷰어"
ZOOMS = [0.45, 0.6, 0.8, 1.0, 1.5, 2.0, 2.8]

MODE_HINT = {
    "cursor": "문장 클릭 = 고정(핀) · 우측 문장 hover = 원문 하이라이트",
    "note": "여백(아이보리 영역)을 클릭하면 포스트잇이 생기고 바로 작성할 수 있습니다",
    "pen": "드래그해서 형광펜으로 표시하세요 (색상 선택 가능)",
    "erase": "지우고 싶은 형광펜 표시를 클릭하세요",
    "bookmark": "중요한 페이지를 클릭하세요 — 별 표시가 토글됩니다",
}

APP_QSS = """
QMainWindow, QDialog { background: #F7F4EC; }
QToolBar { background: #FDFBF6; border: none; border-bottom: 1px solid #E5DECE;
           padding: 4px 8px; spacing: 4px; }
QToolButton { background: transparent; color: #4A4232; border: 1px solid transparent;
              border-radius: 8px; padding: 5px 12px; font-size: 13px; }
QToolButton:hover { background: #EFE8D8; border-color: #DCD2BA; }
QToolButton:checked { background: #F0E2B4; border-color: #E0C570; color: #3C3420; }
QStatusBar { background: #FDFBF6; color: #7A6F58; border-top: 1px solid #E5DECE; }
QMenuBar { background: #FDFBF6; }
QProgressBar { border: 1px solid #DCD2BA; border-radius: 6px; background: #FFFFFF;
               text-align: center; color: #6B5E43; }
QProgressBar::chunk { background: #EBC85E; border-radius: 5px; }
QMessageBox, QInputDialog { background: #FDFBF6; }
QPushButton { background: #F0E2B4; border: 1px solid #E0C570; border-radius: 8px;
              padding: 5px 14px; color: #3C3420; }
QPushButton:hover { background: #EBD894; }
QComboBox { background: #FFFFFF; border: 1px solid #DCD2BA; border-radius: 8px;
            padding: 3px 8px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #D8CEB6; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #C4B896; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #D8CEB6; border-radius: 5px; min-width: 30px; }
"""


class SummaryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("논문 요약 — 읽기 전 준비")
        self.resize(640, 640)
        lay = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setStyleSheet(
            "QTextBrowser { background: #FFFDF6; border: 1px solid #E5DECE;"
            " border-radius: 10px; padding: 14px; font-size: 14px; }")
        lay.addWidget(self.browser, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("닫기")
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        lay.addLayout(row)

    def set_text(self, text):
        self.browser.setPlainText(text)


class MainWindow(QMainWindow):
    def __init__(self, db_path=None):
        super().__init__()
        self.cfg = config.load_config()
        db_path = db_path or Path(__file__).resolve().parent / "translations.db"
        self.cache = cache_mod.Cache(db_path)

        self.segments, self.by_id, self.translations = [], {}, {}
        self.pdf_path, self.file_hash, self.worker = None, None, None
        self.summary_worker = None
        self.summary_text = None
        self._summary_error = None
        self.summary_dialog = None
        self.detail_dialog = None
        self.pinned_id = None
        zi = self.cfg.get("pdf_zoom", 1.5)
        self.zoom_idx = min(range(len(ZOOMS)), key=lambda i: abs(ZOOMS[i] - zi))

        self.resize(1500, 920)
        self.setWindowTitle(APP_TITLE)
        self.setStyleSheet(APP_QSS)

        # --- 중앙 위젯 ---
        self.pdf = PdfView(zoom=ZOOMS[self.zoom_idx])
        self.panel = TranslationPanel()
        self.panel.segmentHovered.connect(self.on_hover)
        self.panel.hoverCleared.connect(self.on_hover_clear)
        self.panel.segmentClicked.connect(self.on_panel_click)
        self.panel.detailRequested.connect(self.on_detail_requested)
        self.pdf.segmentClicked.connect(self.on_pdf_click)
        self.pdf.noteRequested.connect(self.on_note_requested)
        self.pdf.noteMoved.connect(self.on_note_moved)
        self.pdf.noteCommitted.connect(self.on_note_committed)
        self.pdf.noteDeleted.connect(self.on_note_deleted)
        self.pdf.strokeCompleted.connect(self.on_stroke_completed)
        self.pdf.strokeDeleted.connect(self.on_stroke_deleted)
        self.pdf.bookmarkToggled.connect(self.on_bookmark_toggled)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.pdf)
        split.addWidget(self.panel)
        split.setSizes([1080, 420])
        self.setCentralWidget(split)

        # --- 툴바 ---
        tb = self.addToolBar("main")
        tb.setMovable(False)
        self.act_open = QAction("PDF 열기", self)
        self.act_open.triggered.connect(self.choose_pdf)
        tb.addAction(self.act_open)
        self.act_retry = QAction("실패분 재번역", self)
        self.act_retry.setVisible(False)
        self.act_retry.triggered.connect(self.retry_failed)
        tb.addAction(self.act_retry)
        tb.addSeparator()

        self.mode_group = QActionGroup(self)
        self.mode_actions = {}
        for mode, label in (("cursor", "커서"), ("note", "메모"), ("pen", "형광펜"),
                            ("erase", "지우개"), ("bookmark", "중요표시")):
            a = QAction(label, self)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, m=mode: self.set_mode(m))
            self.mode_group.addAction(a)
            tb.addAction(a)
            self.mode_actions[mode] = a
        self.mode_actions["cursor"].setChecked(True)

        self.cmb_color = QComboBox()
        self.cmb_color.addItems(["노랑", "초록", "분홍", "파랑"])
        self.cmb_color.currentTextChanged.connect(self.on_pen_color)
        self.cmb_color.setVisible(False)
        tb.addWidget(self.cmb_color)

        self.act_summary = QAction("요약", self)
        self.act_summary.triggered.connect(self.show_summary)
        tb.addAction(self.act_summary)

        self.act_bm_list = QAction("중요 페이지", self)
        self.act_bm_list.triggered.connect(self.show_bookmark_menu)
        tb.addAction(self.act_bm_list)

        tb.addSeparator()
        for label, fn in (("확대 +", self.zoom_in), ("축소 −", self.zoom_out),
                          ("글자 +", self.panel.font_bigger), ("글자 −", self.panel.font_smaller)):
            a = QAction(label, self)
            a.triggered.connect(fn)
            tb.addAction(a)

        # --- 상태바 ---
        self.lbl_status = QLabel(MODE_HINT["cursor"])
        self.statusBar().addWidget(self.lbl_status)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress)

    # ---------- 모드 ----------
    def set_mode(self, mode):
        self.pdf.set_mode(mode)
        self.cmb_color.setVisible(mode == "pen")
        self.lbl_status.setText(MODE_HINT.get(mode, ""))

    def on_pen_color(self, color):
        self.pdf.pen_color = color

    # ---------- 열기 ----------
    def choose_pdf(self):
        if self.worker and self.worker.isRunning():
            return
        start = str(self.pdf_path.parent) if self.pdf_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "PDF 열기", start, "PDF 파일 (*.pdf)")
        if path:
            self.open_pdf(path)

    def open_pdf(self, path):
        self.lbl_status.setText("문장 추출 중…")
        QApplication.processEvents()
        try:
            segs = extractor.extract_segments(path)
        except Exception as e:
            QMessageBox.critical(self, "열기 실패", f"PDF 텍스트 추출 실패:\n{e}")
            self.lbl_status.setText("")
            return
        if not segs:
            QMessageBox.warning(
                self, "텍스트 없음",
                "이 PDF에는 텍스트 레이어가 없는 것 같습니다(스캔본).\n"
                "이 프로그램은 텍스트 기반 PDF를 지원합니다.")
            self.lbl_status.setText("")
            return
        missing = self.load_document(path, segs)
        self.maybe_start_summary()
        if missing:
            if not config.load_api_key():
                self.lbl_status.setText(
                    "API 키 없음 — 원문만 표시 중. (.env 또는 OPENAI_API_KEY 설정 후 다시 열면 번역)")
                return
            self.start_worker(missing)
        else:
            self.lbl_status.setText(f"저장된 번역을 불러왔습니다: {len(segs)}문장 — API 호출 0건")

    def load_document(self, path, segments):
        """열린 문서를 UI에 반영. 캐시에 없는(번역 필요한) 세그먼트 목록을 반환."""
        self.pdf_path = Path(path)
        self.file_hash = self.cache.hash_file(path)
        self.cache.save_layout(self.file_hash, segments)
        self.segments = segments
        self.by_id = {s["id"]: s for s in segments}
        self.translations = {}
        missing = []
        for s in segments:
            t = self.cache.get_translation(s["src_hash"])
            if t:
                self.translations[s["src_hash"]] = t
            else:
                missing.append(s)
        self.pdf.load(path, zoom=ZOOMS[self.zoom_idx])
        self.pdf.set_segments(segments)
        self.pdf.set_annotation_source(self.cache, self.file_hash)
        self.pdf._load_annotations()
        self.panel.load(segments, self.translations)
        self.setWindowTitle(f"{self.pdf_path.name} — {APP_TITLE}")
        self.act_retry.setVisible(False)
        self.summary_text = self.cache.get_summary(self.file_hash)
        return missing

    # ---------- 요약 ----------
    def maybe_start_summary(self):
        """요약이 캐시에 없고 키가 있으면 열자마자 백그라운드로 자동 생성."""
        if self.summary_text is not None or self.summary_worker is not None:
            return
        if not config.load_api_key():
            return
        self.summary_worker = summarizer.SummaryWorker(
            self.pdf_path, self.cfg.get("summary_model", "gpt-6-luna"),
            config.load_api_key())
        self.summary_worker.ok.connect(self.on_summary_ok)
        self.summary_worker.error.connect(self.on_summary_error)
        self.summary_worker.start()
        self.lbl_status.setText("요약 생성 중… ('요약' 버튼으로 진행 중에도 열 수 있습니다)")

    def on_summary_ok(self, text):
        self.summary_text = text
        self.cache.put_summary(self.file_hash, text, self.cfg.get("summary_model", "gpt-6-luna"))
        self.lbl_status.setText("요약 완료 — 툴바의 '요약' 버튼으로 확인하세요")
        if self.summary_dialog is not None and self.summary_dialog.isVisible():
            self.summary_dialog.set_text(text)

    def on_summary_error(self, msg):
        self.lbl_status.setText("요약 생성 실패")
        self.summary_text = None
        self._summary_error = msg   # '요약' 클릭 시 그대로 보여준다
        if self.summary_dialog is not None and self.summary_dialog.isVisible():
            self.summary_dialog.set_text(f"요약 생성 실패:\n{msg}")

    def show_summary(self):
        if self.summary_dialog is None:
            self.summary_dialog = SummaryDialog(self)
        if self.summary_text is not None:
            self.summary_dialog.set_text(self.summary_text)
        elif self.summary_worker is not None and self.summary_worker.isRunning():
            self.summary_dialog.set_text("요약 생성 중입니다… 잠시 후 이 창에 자동으로 표시됩니다.")
        elif getattr(self, "_summary_error", None):
            self.summary_dialog.set_text(
                f"이전 요약 시도가 실패했습니다:\n{self._summary_error}\n\n"
                "PDF를 다시 열면 자동으로 재시도됩니다.")
            self._summary_error = None
        else:
            self.summary_dialog.set_text(
                "저장된 요약이 없습니다. PDF를 다시 열면 자동 생성됩니다.\n"
                "(API 키가 설정되어 있어야 합니다)")
        self.summary_dialog.show()
        self.summary_dialog.raise_()

    # ---------- 번역 워커 ----------
    def start_worker(self, missing):
        self.worker = translator.TranslationWorker(
            missing, self.cfg["model"], config.load_api_key())
        self.worker.progress.connect(self.on_progress)
        self.worker.partial.connect(self.on_partial)
        self.worker.ok.connect(self.on_translated)
        self.worker.error.connect(self.on_error)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.act_open.setEnabled(False)
        self.act_retry.setVisible(False)
        self.lbl_status.setText(f"번역 중… (모델: {self.cfg['model']})")
        self.worker.start()

    def on_progress(self, done, total):
        self.progress.setValue(int(done * 100 / max(1, total)))
        self.lbl_status.setText(f"번역 중… {done}/{total} 문장")

    def on_partial(self, pairs):
        """배치 1건 완료분: 즉시 저장(중단에도 보존) + 패널 실시간 갱신."""
        text_of = {s["src_hash"]: s["text"] for s in self.segments}
        self.cache.put_translations(
            [(h, text_of.get(h, ""), d) for h, d in pairs.items()],
            self.cfg["model"])
        self.translations.update(pairs)
        self.panel.apply_translations(self.translations)

    def on_translated(self, result):
        self.progress.setVisible(False)
        self.act_open.setEnabled(True)
        text_of = {s["src_hash"]: s["text"] for s in self.segments}
        self.cache.put_translations(
            [(h, text_of.get(h, ""), d) for h, d in result.items()],
            self.cfg["model"])
        self.translations.update(result)
        self.panel.load(self.segments, self.translations)
        if self.pinned_id is not None:
            self.panel.select_segment(self.pinned_id)   # 핀 상태 유지
        remain = [s for s in self.segments if s["src_hash"] not in self.translations]
        if remain:
            self.lbl_status.setText(f"{len(remain)}문장 번역 실패 — 툴바의 '실패분 재번역' 실행 가능")
            self.act_retry.setVisible(True)
        else:
            self.lbl_status.setText(f"번역 완료 ({len(self.segments)}문장) — translations.db에 영구 저장, 다시 열면 그대로 표시됩니다")

    def on_error(self, msg):
        self.progress.setVisible(False)
        self.act_open.setEnabled(True)
        self.lbl_status.setText("번역 오류")
        QMessageBox.warning(self, "번역 오류", msg)

    def retry_failed(self):
        if self.worker and self.worker.isRunning():
            return
        missing = [s for s in self.segments if s["src_hash"] not in self.translations]
        if missing and config.load_api_key():
            self.start_worker(missing)

    # ---------- 동기화 ----------
    def on_hover(self, sid):
        s = self.by_id.get(sid)
        if s:
            self.pdf.highlight([(s["page"], s["bbox"])])

    def on_hover_clear(self):
        self.pdf.clear_highlight()

    def on_panel_click(self, sid):
        """번역 카드 클릭 = 고정(핀) 토글. 같은 문장을 다시 클릭하면 해제."""
        if self.pinned_id == sid:
            self._unpin()
        else:
            self._pin(sid)

    def on_pdf_click(self, sid):
        self._pin(sid)

    def _pin(self, sid):
        self.pinned_id = sid
        s = self.by_id.get(sid)
        if s:
            self.pdf.pin_highlight([(s["page"], s["bbox"])])
            self.panel.select_segment(sid)

    def _unpin(self):
        self.pinned_id = None
        self.pdf.clear_pin()
        self.panel.clearSelection()

    # ---------- 자세히 보기 ----------
    def on_detail_requested(self, sid):
        if self.detail_dialog is None:
            self.detail_dialog = detail_view.DetailDialog(self)
        self.detail_dialog.show_for(self.panel, sid)

    # ---------- 메모 (포스트잇 현장 작성) ----------
    def on_note_requested(self, scene_pos):
        nid = self.pdf.add_note(scene_pos, "")
        self.pdf.start_note_edit(nid)   # 생긴 자리에서 바로 작성

    def on_note_committed(self, note_id):
        """포스트잇 작성 완료(포커스 이탈/Esc 아님=확정): 빈 내용이면 삭제, 아니면 저장."""
        text = self.pdf.note_text(note_id)
        if not text.strip():
            self.pdf.remove_note(note_id)
            self.cache.delete_note(self.file_hash, note_id)
        else:
            page, x, y, t = self.pdf.note_anchor(note_id)
            self.cache.put_note(self.file_hash, note_id, page, x, y, t)

    def on_note_moved(self, note_id):
        if self.pdf.note_text(note_id).strip():
            page, x, y, t = self.pdf.note_anchor(note_id)
            self.cache.put_note(self.file_hash, note_id, page, x, y, t)

    def on_note_deleted(self, note_id):
        self.cache.delete_note(self.file_hash, note_id)

    # ---------- 형광펜 ----------
    def on_stroke_completed(self, page, color, points):
        sid = self.pdf._next_stroke_id - 1   # 방금 추가된 항목
        self.cache.put_stroke(self.file_hash, sid, page, color, points)

    def on_stroke_deleted(self, stroke_id):
        self.cache.delete_stroke(self.file_hash, stroke_id)

    # ---------- 중요 페이지 ----------
    def on_bookmark_toggled(self, page, now):
        self.cache.set_bookmark(self.file_hash, page, now)

    def show_bookmark_menu(self):
        pages = self.pdf.bookmarked_pages()
        if not pages:
            self.lbl_status.setText("중요 표시한 페이지가 없습니다 — 툴바 '중요표시' 모드로 페이지를 클릭하세요.")
            return
        m = QMenu(self)
        for p in pages:
            m.addAction(f"{p + 1}쪽", lambda p=p: self.pdf.scroll_to_page(p))
        m.exec(QCursor.pos())

    # ---------- 확대/축소 ----------
    def zoom_in(self):
        self._set_zoom(self.zoom_idx + 1)

    def zoom_out(self):
        self._set_zoom(self.zoom_idx - 1)

    def _set_zoom(self, idx):
        idx = max(0, min(len(ZOOMS) - 1, idx))
        if idx == self.zoom_idx:
            return
        self.zoom_idx = idx
        self.cfg["pdf_zoom"] = ZOOMS[idx]
        config.save_config_overrides(pdf_zoom=ZOOMS[idx])
        if self.pdf_path:
            self.pdf.load(self.pdf_path, zoom=ZOOMS[idx])
            self.pdf.set_segments(self.segments)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:
        win.open_pdf(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()