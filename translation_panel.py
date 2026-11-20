"""오른쪽 번역 패널: 문장 단위 리스트 + hover/클릭 시그널 + 복사 지원.

- hover: segmentHovered 시그널 (원문 임시 하이라이트)
- 클릭: segmentClicked 시그널 (원문에 고정 하이라이트 = 핀)
- 복사: 드래그 선택 + Ctrl+C 또는 우클릭 > 복사
- 말줄임("...") 방지: 항목 높이를 텍스트가 모두 보이도록 직접 계산
"""
from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMenu

ROLE_ID = Qt.UserRole
ROLE_HASH = Qt.UserRole + 1


class TranslationPanel(QListWidget):
    segmentHovered = Signal(int)   # 문장 id (hover)
    hoverCleared = Signal()
    segmentClicked = Signal(int)   # 문장 id (클릭 = 핀 토글)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)            # itemEntered에 필수
        self.setWordWrap(True)
        self.setUniformItemSizes(False)
        self.setTextElideMode(Qt.ElideNone)    # "..." 말줄임 금지
        self.setSpacing(4)
        self.setSelectionMode(QListWidget.ExtendedSelection)  # 여러 문장 선택 복사
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.itemEntered.connect(self._on_entered)
        self.itemClicked.connect(self._on_clicked)
        self.viewportEntered.connect(self.hoverCleared.emit)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.setStyleSheet(
            "QListWidget { background: #FFFFFF; }"
            "QListWidget::item { padding: 7px 9px; border-radius: 6px; }"
            "QListWidget::item:hover { background: #FFF59D; }"
            "QListWidget::item:selected { background: #FFE082; color: black; }"
        )
        self._font_pt = 11
        self._apply_font()

    # ---------- 시그널 ----------
    def _on_entered(self, item):
        sid = item.data(ROLE_ID)
        if sid is not None:
            self.segmentHovered.emit(int(sid))

    def _on_clicked(self, item):
        sid = item.data(ROLE_ID)
        if sid is not None:
            self.segmentClicked.emit(int(sid))

    def leaveEvent(self, ev):
        self.hoverCleared.emit()
        super().leaveEvent(ev)

    # ---------- 복사 ----------
    def _menu(self, pos):
        if not self.selectedItems():
            return
        m = QMenu(self)
        a = m.addAction("복사")
        if m.exec(self.viewport().mapToGlobal(pos)) == a:
            self.copy_selected()

    def copy_selected(self):
        rows = sorted(self.selectionModel().selectedIndexes(), key=lambda i: i.row())
        text = "\n".join(str(i.data(Qt.DisplayRole) or "") for i in rows)
        if text:
            QGuiApplication.clipboard().setText(text)

    def keyPressEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier and ev.key() == Qt.Key_C:
            self.copy_selected()
            return
        super().keyPressEvent(ev)

    # ---------- 데이터 ----------
    def load(self, segments, translations):
        self.clear()
        for s in segments:
            dst = translations.get(s["src_hash"])
            text = dst if dst else s["text"] + "   (번역 대기/실패)"
            it = QListWidgetItem(text)
            it.setData(ROLE_ID, s["id"])
            it.setData(ROLE_HASH, s["src_hash"])
            it.setToolTip(f"{s['text']}\n\n— {s['page'] + 1}쪽")
            if not dst:
                it.setForeground(Qt.gray)
            self.addItem(it)
        self._reflow()

    def update_translations(self, translations):
        """방금 도착한 번역분만 골라 항목 텍스트를 갱신 (리스트 재구성 없음)."""
        changed = False
        for i in range(self.count()):
            it = self.item(i)
            h = it.data(ROLE_HASH)
            dst = translations.get(h) if h else None
            if dst and it.text() != dst:
                it.setText(dst)
                it.setForeground(Qt.black)
                changed = True
        if changed:
            self._reflow()

    def select_segment(self, sid):
        for i in range(self.count()):
            it = self.item(i)
            if it.data(ROLE_ID) == sid:
                self.setCurrentRow(i)
                self.scrollToItem(it, QListWidget.PositionAtCenter)
                return i
        return None

    # ---------- 레이아웃: "..." 방지의 핵심 ----------
    def _reflow(self):
        """항목 높이를 '텍스트가 감싸지며 모두 보이는 높이'로 직접 계산한다.

        QListWidget의 자동 높이 계산은 창 폭이 변할 때 갱신을 놓쳐 텍스트 끝을
        "...". 으로 잘라버리는 경우가 있다(Windows 한글 폰트에서 특히).
        QFontMetrics로 감싼 높이를 직접 sizeHint에 넣어 이를 막는다.
        """
        fm = QFontMetrics(self.font())
        w = max(120, self.viewport().width() - 26)
        for i in range(self.count()):
            it = self.item(i)
            r = fm.boundingRect(QRect(0, 0, w, 20000), Qt.TextWordWrap, it.text())
            it.setSizeHint(QSize(w, r.height() + 16))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._reflow()

    # ---------- 글자 크기 ----------
    def font_bigger(self):
        self._font_pt = min(22, self._font_pt + 1)
        self._apply_font()

    def font_smaller(self):
        self._font_pt = max(8, self._font_pt - 1)
        self._apply_font()

    def _apply_font(self):
        f = QFont("Malgun Gothic")   # Windows 한글 폰트 (없으면 시스템이 대체)
        f.setPointSize(self._font_pt)
        self.setFont(f)
        self._reflow()
