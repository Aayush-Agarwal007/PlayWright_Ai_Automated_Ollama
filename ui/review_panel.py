"""Panel to review / edit the proposed answer for the selected question."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from services.models import CHOICE_KINDS, TEXT_KINDS, AnswerPlan, Question


class ReviewPanel(QWidget):
    answerConfirmed = Signal(int, object)   # qid, answer (str | list[str])
    skipRequested = Signal(int)
    regenerateRequested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._qid: int | None = None
        self._kind = ""
        lay = QVBoxLayout(self)
        self.title = QLabel("Select a question to review")
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-weight:600;")
        self.meta = QLabel("")
        self.meta.setWordWrap(True)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Proposed answer…")
        self.options = QListWidget()
        self.options.hide()
        row = QHBoxLayout()
        self.btn_confirm = QPushButton("Confirm answer")
        self.btn_skip = QPushButton("Skip")
        self.btn_regen = QPushButton("Regenerate draft")
        for b in (self.btn_confirm, self.btn_skip, self.btn_regen):
            row.addWidget(b)
        for w in (self.title, self.meta, self.editor, self.options):
            lay.addWidget(w)
        lay.addLayout(row)
        self.btn_confirm.clicked.connect(self._confirm)
        self.btn_skip.clicked.connect(lambda: self._qid is not None and self.skipRequested.emit(self._qid))
        self.btn_regen.clicked.connect(lambda: self._qid is not None and self.regenerateRequested.emit(self._qid))
        self.set_enabled(False)

    def set_enabled(self, on: bool) -> None:
        for w in (self.btn_confirm, self.btn_skip, self.btn_regen, self.editor, self.options):
            w.setEnabled(on)

    def show_plan(self, q: Question, plan: AnswerPlan) -> None:
        self._qid, self._kind = q.id, q.kind
        self.title.setText(q.text)
        self.meta.setText(f"Type: {q.kind} · Source: {plan.source} · Confidence: {plan.confidence:.2f} · "
                          f"Status: {plan.status}\n{plan.reason}")
        is_choice = q.kind in CHOICE_KINDS
        self.editor.setVisible(not is_choice)
        self.options.setVisible(is_choice)
        editable = is_choice or q.kind in TEXT_KINDS
        self.set_enabled(editable)
        self.btn_regen.setEnabled(q.kind in ("text", "paragraph"))
        if is_choice:
            self.options.clear()
            self.options.setSelectionMode(
                QAbstractItemView.MultiSelection if q.kind == "checkbox" else QAbstractItemView.SingleSelection)
            chosen = plan.answer if isinstance(plan.answer, list) else [plan.answer]
            self.options.addItems(q.options)
            for i in range(self.options.count()):
                if self.options.item(i).text() in chosen:
                    self.options.item(i).setSelected(True)
        else:
            self.editor.setPlainText(plan.answer_text())

    def _confirm(self) -> None:
        if self._qid is None:
            return
        if self._kind in CHOICE_KINDS:
            labels = [i.text() for i in self.options.selectedItems()]
            if not labels:
                return
            self.answerConfirmed.emit(self._qid, labels if self._kind == "checkbox" else labels[0])
        else:
            text = self.editor.toPlainText().strip()
            if text:
                self.answerConfirmed.emit(self._qid, text)
