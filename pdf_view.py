"""왼쪽 PDF 뷰어.

- 모든 페이지를 QGraphicsScene에 세로로 쌓아 렌더링
- 하이라이트: hover(임시, 연한 노랑) + 클릭 고정(핀, 진한 노랑) — 동시 최대 2개
- 모드: 커서(기본) / 메모(여백 클릭 생성) / 형광펜(드래그) / 지우개 / 중요표시
- 메모·형광펜·중요표시는 PDF 페이지 좌표 기준으로 저장 -> 확대/축소에도 위치 유지
"""
import fitz
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import (QGraphicsPathItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsTextItem, QGraphicsView,
                               QMenu)

HL_FILL = QColor(255, 235, 59, 95)    # hover용 연한 노랑
HL_PEN = QColor(245, 180, 0, 230)
PIN_FILL = QColor(255, 202, 40, 130)  # 핀용 진한 노랑
PIN_PEN = QColor(225, 140, 0, 255)

PEN_COLORS = {  # 형광펜 색상 (이름 -> RGB)
    "노랑": (255, 238, 88), "초록": (167, 255, 117),
    "분홍": (255, 138, 180), "파랑": (130, 200, 255),
}
KIND = 0  # item data role: ("stroke", id) / ("badge", page)


class NoteItem(QGraphicsRectItem):
    """여백에 붙이는 메모지. 드래그로 이동, 더블클릭/우클릭으로 편집·삭제."""

    def __init__(self, note_id, text, view):
        super().__init__(0, 0, 190, 90)
        self.note_id = note_id
        self.view = view
        self.was_moved = False
        self.setFlags(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsRectItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setBrush(QBrush(QColor(255, 236, 140, 242)))
        self.setPen(QPen(QColor(230, 160, 0), 1.4))
        self.setZValue(15)
        self._text = QGraphicsTextItem(self)
        f = QFont()
        f.setPointSize(10)
        self._text.setFont(f)
        self._text.setPos(9, 7)
        self._text.setTextWidth(172)
        self.set_text(text)

    def set_text(self, text):
        self._text.setPlainText(text)
        h = min(300, self._text.boundingRect().height() + 16)
        self.setRect(0, 0, 190, h)

    def text(self):
        return self._text.toPlainText()

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.GraphicsItemChange.ItemPositionHasChanged:
            self.was_moved = True
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, ev):
        self.view.noteEditRequested.emit(self.note_id)
        super().mouseDoubleClickEvent(ev)

    def contextMenuEvent(self, ev):
        m = QMenu()
        a_edit = m.addAction("메모 편집")
        a_del = m.addAction("메모 삭제")
        chosen = m.exec(ev.screenPos())
        if chosen == a_edit:
            self.view.noteEditRequested.emit(self.note_id)
        elif chosen == a_del:
            self.view.remove_note(self.note_id)
            self.view.noteDeleted.emit(self.note_id)
        ev.accept()


class PdfView(QGraphicsView):
    segmentClicked = Signal(int)
    noteRequested = Signal(object)            # QPointF(장면 좌표)
    noteMoved = Signal(int)
    noteEditRequested = Signal(int)
    noteDeleted = Signal(int)
    strokeCompleted = Signal(int, str, list)  # page, color, [(x,y) 페이지 좌표]
    strokeDeleted = Signal(int)
    bookmarkToggled = Signal(int, bool)       # page, 이제 중요표시인가

    def __init__(self, zoom=1.5, parent=None):
        super().__init__(parent)
        self.zoom = zoom
        self.doc = None
        self.page_offsets = []
        self.page_sizes = []
        self._segments = []
        self._hl_items = []     # hover용 (임시)
        self._pin_items = []    # 클릭 고정용
        self.mode = "cursor"
        self.pen_color = "노랑"
        self._cache = None
        self._fh = None
        self._notes = {}        # note_id -> NoteItem
        self._strokes = {}      # stroke_id -> QGraphicsPathItem
        self._badges = {}       # page -> badge item
        self._next_note_id = 1
        self._next_stroke_id = 1
        self._pen_pts = None    # 드래그 중 임시 점들(장면 좌표)
        self._pen_item = None
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setBackgroundBrush(QColor(72, 72, 72))
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)

    # ---------- 로딩 ----------
    def load(self, path, zoom=None):
        self.clear_highlight()
        self.clear_pin()
        self.scene_.clear()
        self._notes, self._strokes, self._badges = {}, {}, {}
        if self.doc is not None:
            self.doc.close()
        if zoom:
            self.zoom = zoom
        self.doc = fitz.open(str(path))
        self.page_offsets, self.page_sizes = [], []
        z = self.zoom if self.doc.page_count <= 60 else min(self.zoom, 1.2)  # 메모리 보호
        self.zoom = z
        y = 0.0
        for page in self.doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
            self.scene_.addPixmap(QPixmap.fromImage(img.copy())).setPos(0, y)
            self.page_offsets.append(y)
            self.page_sizes.append((pix.width, pix.height))
            y += pix.height + 14
        self.setSceneRect(self.scene_.itemsBoundingRect())
        self._load_annotations()

    def set_annotation_source(self, cache_db, file_hash):
        self._cache, self._fh = cache_db, file_hash

    def _load_annotations(self):
        """캐시에 저장된 메모/형광펜/중요표시를 다시 그린다(확대/축소 후 포함)."""
        if not self._cache or not self._fh:
            return
        notes = self._cache.get_notes(self._fh)
        self._next_note_id = max([n["note_id"] for n in notes], default=0) + 1
        for n in notes:
            self._spawn_note(n["note_id"], n["page"], n["x_frac"], n["y_frac"], n["text"])
        strokes = self._cache.get_strokes(self._fh)
        self._next_stroke_id = max([s["stroke_id"] for s in strokes], default=0) + 1
        for s in strokes:
            self._draw_stroke(s["stroke_id"], s["page"], s["color"], s["points"])
        for pno in self._cache.get_bookmarks(self._fh):
            self._spawn_badge(pno)
        self.setSceneRect(self.scene_.itemsBoundingRect())

    # ---------- 좌표 유틸 ----------
    def _page_at(self, y):
        for pno, off in enumerate(self.page_offsets):
            h = self.page_sizes[pno][1]
            if off <= y <= off + h:
                return pno
        return None

    def _page_nearest(self, y):
        best, dist = 0, 1e18
        for pno, off in enumerate(self.page_offsets):
            h = self.page_sizes[pno][1]
            d = 0 if off <= y <= off + h else min(abs(y - off), abs(y - off - h))
            if d < dist:
                best, dist = pno, d
        return best

    def _anchor_of(self, scene_pos):
        """장면 좌표 -> (page, x_frac, y_frac). frac은 페이지 폭/높이 기준."""
        pno = self._page_nearest(scene_pos.y())
        w, h = self.page_sizes[pno]
        x = scene_pos.x() / w
        y = (scene_pos.y() - self.page_offsets[pno]) / h
        return pno, x, y

    def _pos_of(self, pno, x_frac, y_frac):
        w, h = self.page_sizes[pno]
        from PySide6.QtCore import QPointF
        return QPointF(x_frac * w, self.page_offsets[pno] + y_frac * h)

    # ---------- 하이라이트 ----------
    def highlight(self, page_rects):
        self.clear_highlight()
        first = None
        for pno, (x0, y0, x1, y1) in page_rects:
            if not (0 <= pno < len(self.page_offsets)):
                continue
            r = QRectF(x0 * self.zoom,
                       self.page_offsets[pno] + y0 * self.zoom,
                       (x1 - x0) * self.zoom,
                       (y1 - y0) * self.zoom).adjusted(-2, -2, 2, 2)
            it = QGraphicsRectItem(r)
            it.setBrush(QBrush(HL_FILL))
            it.setPen(QPen(HL_PEN, 1.2))
            it.setZValue(10)
            self.scene_.addItem(it)
            self._hl_items.append(it)
            if first is None:
                first = it
        if first is not None:
            self.ensureVisible(first, 90, 90)

    def clear_highlight(self):
        for it in self._hl_items:
            self.scene_.removeItem(it)
        self._hl_items = []

    def pin_highlight(self, page_rects):
        """클릭으로 고정된 하이라이트. hover와 별개로 유지(최대 2개 동시 표시)."""
        self.clear_pin()
        first = None
        for pno, (x0, y0, x1, y1) in page_rects:
            if not (0 <= pno < len(self.page_offsets)):
                continue
            r = QRectF(x0 * self.zoom,
                       self.page_offsets[pno] + y0 * self.zoom,
                       (x1 - x0) * self.zoom,
                       (y1 - y0) * self.zoom).adjusted(-3, -3, 3, 3)
            it = QGraphicsRectItem(r)
            it.setBrush(QBrush(PIN_FILL))
            it.setPen(QPen(PIN_PEN, 1.8))
            it.setZValue(11)
            self.scene_.addItem(it)
            self._pin_items.append(it)
            if first is None:
                first = it
        if first is not None:
            self.ensureVisible(first, 90, 90)

    def clear_pin(self):
        for it in self._pin_items:
            self.scene_.removeItem(it)
        self._pin_items = []

    def scroll_to_page(self, pno):
        if 0 <= pno < len(self.page_offsets):
            self.centerOn(self.page_sizes[pno][0] / 2,
                          self.page_offsets[pno] + self.page_sizes[pno][1] * 0.3)

    # ---------- 메모 ----------
    def _spawn_note(self, note_id, page, x_frac, y_frac, text):
        it = NoteItem(note_id, text, self)
        it.setPos(self._pos_of(page, x_frac, y_frac))
        self.scene_.addItem(it)
        self._notes[note_id] = it
        return it

    def add_note(self, scene_pos, text):
        page, x, y = self._anchor_of(scene_pos)
        nid = self._next_note_id
        self._next_note_id += 1
        self._spawn_note(nid, page, x, y, text)
        self.setSceneRect(self.scene_.itemsBoundingRect())
        return nid

    def note_anchor(self, note_id):
        it = self._notes[note_id]
        page, x, y = self._anchor_of(it.pos())
        return page, x, y, it.text()

    def update_note_text(self, note_id, text):
        self._notes[note_id].set_text(text)
        self.setSceneRect(self.scene_.itemsBoundingRect())

    def remove_note(self, note_id):
        it = self._notes.pop(note_id, None)
        if it is not None:
            self.scene_.removeItem(it)

    def note_text(self, note_id):
        return self._notes[note_id].text()

    # ---------- 형광펜 ----------
    def _draw_stroke(self, stroke_id, page, color, points):
        rgb = PEN_COLORS.get(color, PEN_COLORS["노랑"])
        path = None
        from PySide6.QtGui import QPainterPath
        pp = QPainterPath()
        for i, (x, y) in enumerate(points):
            sx, sy = x * self.zoom, self.page_offsets[page] + y * self.zoom
            (pp.moveTo(sx, sy) if i == 0 else pp.lineTo(sx, sy))
        path = pp
        it = QGraphicsPathItem(path)
        it.setPen(QPen(QColor(*rgb, 110), 16, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        it.setZValue(9)
        it.setData(KIND, ("stroke", stroke_id))
        self.scene_.addItem(it)
        self._strokes[stroke_id] = it
        return it

    def add_stroke(self, page, points, color):
        sid = self._next_stroke_id
        self._next_stroke_id += 1
        self._draw_stroke(sid, page, color, points)
        return sid

    def _erase_at(self, scene_pos):
        it = self.scene_.items(scene_pos)
        for item in it:
            d = item.data(KIND)
            if d and d[0] == "stroke":
                sid = d[1]
                self._strokes.pop(sid, None)
                self.scene_.removeItem(item)
                self.strokeDeleted.emit(sid)
                return

    # ---------- 중요 페이지 ----------
    def _spawn_badge(self, pno):
        w = self.page_sizes[pno][0]
        badge = QGraphicsRectItem(QRectF(w - 36, 8, 28, 24))
        badge.setBrush(QBrush(QColor(255, 179, 0, 235)))
        badge.setPen(QPen(QColor(180, 110, 0), 1.2))
        badge.setZValue(15)
        badge.setData(KIND, ("badge", pno))
        star = QGraphicsTextItem("★", badge)
        star.setPos(4, 3)   # 배지 안쪽(부모 기준) 좌표
        self.scene_.addItem(badge)
        self._badges[pno] = badge
        return badge

    def toggle_bookmark_page(self, pno):
        if pno in self._badges:
            self.scene_.removeItem(self._badges.pop(pno))
            now = False
        else:
            self._spawn_badge(pno)
            now = True
        self.bookmarkToggled.emit(pno, now)
        return now

    def bookmarked_pages(self):
        return sorted(self._badges.keys())

    # ---------- 모드 ----------
    def set_mode(self, mode):
        self.mode = mode
        self._pen_pts, self._pen_item = None, None
        cur = {"cursor": Qt.ArrowCursor, "note": Qt.PointingHandCursor,
               "pen": Qt.CrossCursor, "erase": Qt.CrossCursor,
               "bookmark": Qt.PointingHandCursor}.get(mode, Qt.ArrowCursor)
        self.viewport().setCursor(cur)

    # ---------- 마우스 ----------
    def mousePressEvent(self, ev):
        sp = self.mapToScene(ev.position().toPoint())
        if ev.button() == Qt.LeftButton:
            if self.mode == "note":
                if not self.page_sizes:
                    return
                w = self.page_sizes[self._page_nearest(sp.y())][0]
                on_page = self._page_at(sp.y()) is not None and 0 <= sp.x() <= w
                if not on_page:   # 페이지 본문 위가 아니라 여백(회색)일 때만
                    self.noteRequested.emit(sp)
                return
            if self.mode == "pen":
                self._pen_pts = [sp]
                self._pen_item = QGraphicsPathItem()
                rgb = PEN_COLORS.get(self.pen_color, PEN_COLORS["노랑"])
                self._pen_item.setPen(QPen(QColor(*rgb, 110), 16, Qt.SolidLine,
                                           Qt.RoundCap, Qt.RoundJoin))
                self._pen_item.setZValue(9)
                self.scene_.addItem(self._pen_item)
                return
            if self.mode == "erase":
                self._erase_at(sp)
                return
            if self.mode == "bookmark":
                self.toggle_bookmark_page(self._page_nearest(sp.y()))
                return
            # 커서 모드: 문장 클릭
            pno = self._page_at(sp.y())
            if pno is not None:
                px = sp.x() / self.zoom
                py = (sp.y() - self.page_offsets[pno]) / self.zoom
                for s in self._segments:
                    if s["page"] != pno:
                        continue
                    x0, y0, x1, y1 = s["bbox"]
                    if x0 - 1 <= px <= x1 + 1 and y0 - 1 <= py <= y1 + 1:
                        self.segmentClicked.emit(int(s["id"]))
                        break
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self.mode == "pen" and self._pen_pts is not None:
            sp = self.mapToScene(ev.position().toPoint())
            self._pen_pts.append(sp)
            pp = self._pen_item.path()
            pp.lineTo(sp.x(), sp.y())
            self._pen_item.setPath(pp)
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self.mode == "pen" and self._pen_pts is not None:
            pts = self._pen_pts
            tmp, self._pen_item = self._pen_item, None
            self._pen_pts = None
            if tmp is not None:
                self.scene_.removeItem(tmp)
            if len(pts) >= 2:
                pno = self._page_nearest(pts[0].y())
                page_pts = [(p.x() / self.zoom,
                             (p.y() - self.page_offsets[pno]) / self.zoom) for p in pts]
                self.add_stroke(pno, page_pts, self.pen_color)
                self.strokeCompleted.emit(pno, self.pen_color, page_pts)
            return
        if self._notes:
            moved = [nid for nid, n in self._notes.items() if n.was_moved]
            for nid in moved:
                self._notes[nid].was_moved = False
                self.noteMoved.emit(nid)
        super().mouseReleaseEvent(ev)

    def set_segments(self, segments):
        self._segments = segments
