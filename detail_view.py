"""자세히 보기 팝업: 한글 번역(위) + 영문 원문(아래), 둘 다 드래그 선택·복사 가능."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                               QTextBrowser, QVBoxLayout)


class DetailDialog(QDialog):
    """번역 카드 우클릭 '자세히 보기'.

    - 위: 한글 번역 / 아래: 영문 원문 (구분선)
    - 두 텍스트 모두 QTextBrowser(TextSelectableByMouse)라 드래그 선택 + Ctrl+C 가능
    - 창이 떠 있는 동안 유지되므로 툴팁처럼 사라지지 않는다
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("자세히 보기 — 번역 / 원문")
        self.resize(720, 560)
        lay = QVBoxLayout(self)

        t_ko = QLabel("한글 번역")
        t_ko.setStyleSheet("color:#6B5E43; font-weight:600;")
        lay.addWidget(t_ko)
        self.ko = QTextBrowser()
        self.ko.setStyleSheet(
            "QTextBrowser { background:#FFFDF6; border:1px solid #E5DECE;"
            " border-radius:10px; padding:10px; font-size:14px; }")
        lay.addWidget(self.ko, 3)

        t_en = QLabel("영문 원문")
        t_en.setStyleSheet("color:#6B5E43; font-weight:600;")
        lay.addWidget(t_en)
        self.en = QTextBrowser()
        self.en.setStyleSheet(
            "QTextBrowser { background:#FFFFFF; border:1px solid #E5DECE;"
            " border-radius:10px; padding:10px; font-size:13px; color:#444444; }")
        lay.addWidget(self.en, 2)

        row = QHBoxLayout()
        self.lbl_copy = QLabel("Ctrl+C로 복사할 수 있습니다")
        self.lbl_copy.setStyleSheet("color:#A79B7E;")
        row.addWidget(self.lbl_copy)
        row.addStretch(1)
        btn = QPushButton("닫기")
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        lay.addLayout(row)

    def set_content(self, ko_text, en_text):
        self.ko.setPlainText(ko_text)
        self.en.setPlainText(en_text)

    def show_for(self, panel, seg_id):
        """카드 목록에서 한글/영문을 찾아 세팅하고 모달리스 표시."""
        ko = en = None
        for s_id, card in panel._cards:
            if s_id == seg_id:
                ko = card.text
                en = card.src_text
                break
        if ko is None:
            return
        if "(번역 대기/실패)" in ko:
            ko = ko.replace("   (번역 대기/실패)", "")
        self.set_content(ko, en or "(원문 없음)")
        self.show()
        self.raise_()