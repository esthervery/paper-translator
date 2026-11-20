"""오른쪽 번역 패널 (QScrollArea + 카드 라벨).

설계 변경 이유:
- 기존 QListWidget은 창 폭이 변할 때 항목 높이 계산을 놓쳐 텍스트 끝을 "..."으로
  잘랐다. QLabel(+WordWrap)은 폭이 변하면 높이가 자동으로 늘어나므로 말줄임이
  원천적으로 발생하지 않는다.
- 페이지 경계마다 헤더("N쪽")를 넣어 번역도 페이지 단위로 구분된다.
- 본문은 마우스로 드래그 선택해 복사할 수 있다(TextSelectableByMouse).
- hover / 클릭(핀) 시그널은 카드 위젯의 enterEvent / mousePressEvent에서 발생.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu,
                               QScrollArea, QVBoxLayout, QWidget)

CARD_QSS = """
QFrame#segCard {
    background: #FFFFFF;
    border: 1px solid #E8E2D4;
    border-radius: 10px;
}
QFrame#segCard[state="hover"] {
    background: #FFF8DB;
    border: 1px solid #F0D060;
}
QFrame#segCard[state="pinned"] {
    background: #FFF1B8;
    border: 1.5px solid #E8A400;
}
QFrame#segCard[state="pinnedHover"] {
    background: #FFEFA6;
    border: 1.5px solid #D89800;
}
QLabel#segText { background: transparent; color: #222222; }
QLabel#segText[failed="true"] { color: #9A9A9A; }
QFrame#pageHeader {
    background: #EFE9DC;
    border: none;
    border-radius: 9px;
}
QLabel#pageHeaderText { background: transparent; color: #6B5E43; font-weight: 600; }
"""


class _Card(QFrame):
    """문장 하나(번역 1세그먼트)를 보여주는 카드."""

    def __init__(self, seg_id, text, failed, src_text="", panel=None):
        super().__init__()
        self.setObjectName("segCard")
        self.seg_id = seg_id
        self.text = text
        self.src_text = src_text          # 영문 원문 (자세히 보기용)
        self._panel_ref = panel           # 시그널 발행용 패널 참조
        self._hover = False
        self._pinned = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        self.lbl = QLabel(text)
        self.lbl.setObjectName("segText")
        self.lbl.setWordWrap(True)                     # 말줄임 원천 차단
        self.lbl.setTextInteractionFlags(Qt.TextSelectableByMouse
                                         | Qt.TextSelectableByKeyboard)
        lay.addWidget(self.lbl)
        self._state = "normal"
        self.set_state("failed" if failed else "normal")

    # --- 상태 시각화 ---
    def set_state(self, state):
        self._state = state
        self.setProperty("state", state)
        self.lbl.setProperty("failed", "true" if state == "failed" else "false")
        st = self.style()
        st.unpolish(self); st.polish(self)
        st.unpolish(self.lbl); st.polish(self.lbl)

    def set_pinned(self, pinned):
        self.refresh(pinned)

    def refresh(self, pinned=None):
        cur = pinned if pinned is not None else (self._state == "pinned")
        base = "pinned" if cur else ("failed" if self._state == "failed" else "normal")
        self.set_state(base + ("Hover" if self._hover and cur else ""))

    # --- 이벤트 ---
    def enterEvent(self, ev):
        self._hover = True
        was = self._state
        if not self._state.startswith("pinned"):
            self.set_state("hover")
        else:
            self.set_state("pinnedHover")
        self._prev_state = was
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.set_state(self._prev_state if hasattr(self, "_prev_state") else "normal")
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._panel_ref.segmentClicked.emit(self.seg_id)
        super().mousePressEvent(ev)

    def enterEvent(self, ev):
        # 진짜 마우스 진입에서만 hover 시그널 -> 원문 하이라이트 추적
        self._panel_ref.segmentHovered.emit(self.seg_id)
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._panel_ref.hoverCleared.emit()
        super().leaveEvent(ev)

    def contextMenuEvent(self, ev):
        m = QMenu(self)
        a = m.addAction("자세히 보기")
        chosen = m.exec(ev.globalPos())
        if chosen == a:
            self._panel_ref.detailRequested.emit(self.seg_id)
        ev.accept()


class _PageHeader(QFrame):
    def __init__(self, page_no):
        super().__init__()
        self.setObjectName("pageHeader")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        t = QLabel(f"— {page_no + 1}쪽 —")
        t.setObjectName("pageHeaderText")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)


class TranslationPanel(QScrollArea):
    segmentHovered = Signal(int)
    hoverCleared = Signal()
    segmentClicked = Signal(int)   # 클릭 = 핀 토글
    detailRequested = Signal(int)  # 우클릭 '자세히 보기'

    def __init__(self, parent=None):
        super().__init__(parent)
        self._panel = self   # 카드에서 시그널 접근용
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._font_pt = 11
        self._cards = []            # [(seg_id, card)]
        self._hash_of_id = {}
        self._pinned_id = None
        self.setStyleSheet(CARD_QSS)

        body = QWidget()
        self._lay = QVBoxLayout(body)
        self._lay.setContentsMargins(10, 10, 10, 10)
        self._lay.setSpacing(6)
        self._lay.addStretch(1)
        self.setWidget(body)

    # ---------- 데이터 ----------
    def load(self, segments, translations):
        self._clear()
        self._hash_of_id = {s["id"]: s["src_hash"] for s in segments}
        cur_page = None
        for s in segments:
            if s["page"] != cur_page:          # 페이지 경계 헤더
                cur_page = s["page"]
                self._lay.addWidget(_PageHeader(cur_page))
            dst = translations.get(s["src_hash"])
            text = dst if dst else s["text"] + "   (번역 대기/실패)"
            card = _Card(s["id"], text, failed=not dst, src_text=s["text"], panel=self)
            self._cards.append((s["id"], card))
            self._lay.insertWidget(self._lay.count() - 1, card)
        self._apply_font()
        if self._pinned_id is not None:
            self.select_segment(self._pinned_id)

    def _clear(self):
        self._cards = []
        while self._lay.count() > 1:        # 마지막 stretch는 유지
            it = self._lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

    def apply_translations(self, translations):
        """번역이 도착한 카드만 텍스트 교체(리스트 재구성 없음, 스크롤 위치 유지)."""
        for sid, card in self._cards:
            dst = translations.get(self._hash_of_id.get(sid))
            if dst and card.text != dst:
                card.text = dst
                card.lbl.setText(dst)
                card.set_state("pinned" if sid == self._pinned_id else "normal")

    def select_segment(self, sid):
        """핀 표시 + 해당 카드로 스크롤."""
        self._pinned_id = sid
        for s_id, card in self._cards:
            card.set_pinned(s_id == sid)
        for s_id, card in self._cards:
            if s_id == sid:
                self.ensureWidgetVisible(card, 30, 60)
                return s_id
        return None

    def clearSelection(self):
        self._pinned_id = None
        for _sid, card in self._cards:
            card.set_pinned(False)

    # ---------- 복사(보조): 드래그 선택 외에 지정 카드 전체 복사 ----------
    def copy_card(self, idx):
        if 0 <= idx < len(self._cards):
            QGuiApplication.clipboard().setText(self._cards[idx][1].text)

    # ---------- hover 해제 ----------
    def leaveEvent(self, ev):
        self.hoverCleared.emit()
        super().leaveEvent(ev)

    # ---------- 글자 크기 ----------
    def font_bigger(self):
        self._font_pt = min(22, self._font_pt + 1)
        self._apply_font()

    def font_smaller(self):
        self._font_pt = max(8, self._font_pt - 1)
        self._apply_font()

    def _apply_font(self):
        f = QFont("Malgun Gothic")
        f.setPointSize(self._font_pt)
        for _sid, card in self._cards:
            card.lbl.setFont(f)