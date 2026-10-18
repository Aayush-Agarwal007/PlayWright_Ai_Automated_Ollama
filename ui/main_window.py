"""SmartForm AI main window. UI only: all heavy work runs in TaskWorker threads."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from services.answer_planner import AnswerPlanner
from services.browser_service import BrowserService
from services.form_inspector import InspectionResult
from services.models import (
    CHOICE_KINDS, SOURCE_AI, SOURCE_PROFILE, SOURCE_USER, ST_ANALYZED, ST_FAILED, ST_FILLED,
    ST_REVIEW, ST_SKIPPED, ST_VERIFIED, AnswerPlan, FillResult, Question,
)
from services.notification_service import notify
from services.ollama_service import OllamaService
from services.profile_service import ProfileService, load_config, save_config
from services.validation_service import validate_form_url
from ui.profile_dialog import ProfileDialog
from ui.review_panel import ReviewPanel
from ui.settings_dialog import SettingsDialog
from ui.workers import TaskWorker

log = logging.getLogger(__name__)
COLUMNS = ["Question", "Input type", "Matched profile field", "Proposed answer", "Source", "Confidence", "Status"]
ROW_COLORS = {ST_VERIFIED: "#c8e6c9", ST_FILLED: "#dcedc8", ST_FAILED: "#ffcdd2",
              ST_REVIEW: "#ffe0b2", ST_SKIPPED: "#e0e0e0"}
AI_COLOR = "#fff59d"
STYLE = """
QMainWindow, QWidget { font-size: 13px; }
#header { background:#1f2a44; border-radius:8px; }
#header QLabel { color:white; }
QPushButton { padding:6px 14px; border-radius:6px; background:#3b5bdb; color:white; }
QPushButton:disabled { background:#b0b7c3; }
QPushButton#danger { background:#c92a2a; }
QPushButton#flat { background:#e9ecef; color:#212529; }
QLineEdit { padding:6px; border:1px solid #adb5bd; border-radius:6px; }
"""


class MainWindow(QMainWindow):
    def __init__(self, base_dir: Path) -> None:
        super().__init__()
        self.base = base_dir
        self.config_path = base_dir / "config.json"
        self.config = load_config(self.config_path)
        self.profile = ProfileService(base_dir / "profile.json", base_dir / "profile.example.json")
        self._init_services()
        self.questions: list[Question] = []
        self.plans: dict[int, AnswerPlan] = {}
        self.results: dict[int, FillResult] = {}
        self.worker: TaskWorker | None = None
        self._status_worker: TaskWorker | None = None
        self._side_workers: list[TaskWorker] = []
        self.setWindowTitle("SmartForm AI — Local Google Forms Autofill Assistant")
        self.resize(1280, 820)
        self.setStyleSheet(STYLE)
        self._build_ui()
        try:
            self.profile.load()
        except Exception as exc:
            self.log(f"Could not read profile.json: {exc}")
        self.timer = QTimer(self, interval=8000)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start()
        QTimer.singleShot(200, self.refresh_status)

    # ---------- setup ----------
    def _init_services(self) -> None:
        self.ollama = OllamaService(self.config)
        self.browser = BrowserService(self.config)

    def _build_ui(self) -> None:
        root = QWidget(); self.setCentralWidget(root)
        lay = QVBoxLayout(root)

        header = QFrame(objectName="header"); hl = QHBoxLayout(header)
        title = QLabel("<b style='font-size:18px'>SmartForm AI</b><br>"
                       "<span style='color:#ced4da'>Local Google Forms autofill — you always press Submit.</span>")
        self.lbl_ollama, self.lbl_model, self.lbl_brave = QLabel("Ollama: …"), QLabel("Model: …"), QLabel("Brave: …")
        hl.addWidget(title, 1)
        for w in (self.lbl_ollama, self.lbl_model, self.lbl_brave):
            hl.addWidget(w)
        btn_profile = QPushButton("Profile", objectName="flat"); btn_settings = QPushButton("Settings", objectName="flat")
        btn_profile.clicked.connect(self.open_profile); btn_settings.clicked.connect(self.open_settings)
        hl.addWidget(btn_profile); hl.addWidget(btn_settings)
        lay.addWidget(header)

        row = QHBoxLayout()
        self.url_edit = QLineEdit(); self.url_edit.setPlaceholderText("Paste a Google Forms URL (https://docs.google.com/forms/…)")
        self.btn_analyze = QPushButton("Analyze Form"); self.btn_fill = QPushButton("Fill Form")
        self.btn_stop = QPushButton("Stop", objectName="danger")
        self.btn_analyze.clicked.connect(self.analyze); self.btn_fill.clicked.connect(self.fill)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_fill.setEnabled(False); self.btn_stop.setEnabled(False)
        row.addWidget(self.url_edit, 1)
        for b in (self.btn_analyze, self.btn_fill, self.btn_stop):
            row.addWidget(b)
        lay.addLayout(row)

        prow = QHBoxLayout()
        self.progress = QProgressBar(); self.progress.setRange(0, 100)
        self.lbl_progress = QLabel("Idle")
        prow.addWidget(self.progress, 1); prow.addWidget(self.lbl_progress, 2)
        lay.addLayout(prow)

        grid = QGridLayout(); self.stat_labels: dict[str, QLabel] = {}
        for i, key in enumerate(["Questions detected", "Profile answers", "AI drafts", "Choices selected",
                                 "Need your input", "Failed fields"]):
            lbl = QLabel(f"{key}: 0"); self.stat_labels[key] = lbl
            grid.addWidget(lbl, 0, i)
        lay.addLayout(grid)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        hh = self.table.horizontalHeader(); hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for i, w in enumerate([300, 90, 150, 260, 100, 80, 100]):
            self.table.setColumnWidth(i, w)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.review = ReviewPanel()
        self.review.answerConfirmed.connect(self._on_confirm)
        self.review.skipRequested.connect(self._on_skip)
        self.review.regenerateRequested.connect(self._on_regenerate)
        top = QSplitter(Qt.Horizontal); top.addWidget(self.table); top.addWidget(self.review)
        top.setStretchFactor(0, 3); top.setStretchFactor(1, 1)
        self.log_box = QPlainTextEdit(readOnly=True); self.log_box.setMaximumBlockCount(2000)
        self.log_box.setPlaceholderText("Diagnostics and errors appear here…")
        split = QSplitter(Qt.Vertical); split.addWidget(top); split.addWidget(self.log_box)
        split.setStretchFactor(0, 4); split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

    # ---------- helpers ----------
    def log(self, msg: str) -> None:
        self.log_box.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _busy(self, on: bool) -> None:
        self.btn_analyze.setEnabled(not on)
        self.btn_fill.setEnabled(not on and bool(self.questions))
        self.btn_stop.setEnabled(on)
        self.review.setEnabled(not on)
        if not on:
            self.progress.setValue(0)

    def _start(self, fn, on_ok, label: str, on_item=None) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        w = TaskWorker(fn, self)
        w.progress.connect(self._on_progress)
        if on_item is not None:
            w.item.connect(on_item)
        w.succeeded.connect(on_ok)
        w.failed.connect(self._on_failed)
        w.cancelled.connect(self._on_cancelled)
        w.finished.connect(lambda: self._busy(False))
        self.worker = w
        self.lbl_progress.setText(label); self._busy(True)
        w.start()

    def _on_progress(self, msg: str, pct: int) -> None:
        self.lbl_progress.setText(msg)
        if pct >= 0:
            self.progress.setValue(pct)

    def _on_failed(self, msg: str) -> None:
        self.log(f"ERROR: {msg}")
        self.lbl_progress.setText("Failed")
        QMessageBox.warning(self, "Operation failed", msg)

    def _on_cancelled(self) -> None:
        self.log("Operation stopped by user.")
        self.lbl_progress.setText("Stopped")

    # ---------- status ----------
    def refresh_status(self) -> None:
        if self._status_worker is not None and self._status_worker.isRunning():
            return
        w = TaskWorker(lambda _w: (self.ollama.check(), self.browser.check_connection()), self)
        w.succeeded.connect(self._apply_status)
        self._status_worker = w
        w.start()

    def _apply_status(self, result) -> None:
        st, (brave_ok, brave_msg) = result
        dot = lambda ok: "<span style='color:%s'>●</span>" % ("#51cf66" if ok else "#ff6b6b")
        self.lbl_ollama.setText(f"Ollama {dot(st.reachable)}"); self.lbl_ollama.setToolTip(st.message)
        self.lbl_model.setText(f"Model: {st.model} {dot(st.model_available)}"); self.lbl_model.setToolTip(st.message)
        self.lbl_brave.setText(f"Brave {dot(brave_ok)}"); self.lbl_brave.setToolTip(brave_msg)

    # ---------- dialogs ----------
    def open_profile(self) -> None:
        try:
            self.profile.load()
        except Exception as exc:
            QMessageBox.warning(self, "Profile", f"profile.json has a problem: {exc}"); return
        if ProfileDialog(self.profile, self).exec():
            self.log("Profile saved. Re-run Analyze to apply changes.")

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():
            self.config.update(dlg.result_config())
            save_config(self.config_path, self.config)
            self.ollama.reload(self.config)
            self.browser.config = self.config
            self.refresh_status(); self.log("Settings saved.")

    # ---------- analyze ----------
    def analyze(self) -> None:
        url = self.url_edit.text().strip()
        ok, msg = validate_form_url(url)
        if not ok:
            QMessageBox.warning(self, "Invalid URL", msg); return
        try:
            self.profile.load()
        except Exception as exc:
            QMessageBox.warning(self, "Profile", f"profile.json problem: {exc}"); return
        st = self.ollama.check()
        ai = self.ollama if st.ok else None
        if ai is None:
            self.log(f"Ollama unavailable ({st.message}). Profile answers only; no AI drafts.")
        planner = AnswerPlanner(self.profile, ai, self.config)

        def task(w: TaskWorker):
            w.report("Opening form in Brave…", 10)
            result = self.browser.open_and_inspect(url, w.cancel_event)
            w.report(f"Detected {len(result.questions)} questions. Planning answers…", 30)
            plans = planner.plan(result.questions, w.cancel_event, w.report)
            return result, plans, planner

        self._start(task, self._on_analyzed, "Analyzing…")

    def _on_analyzed(self, payload) -> None:
        result: InspectionResult; result, plans, planner = payload
        self.questions = result.questions
        self.plans = {p.qid: p for p in plans}
        self.results = {}
        for n in result.notes + planner.warnings:
            self.log(n)
        for d in planner.decisions:
            self.log(f"match: {d}")
        self.log(f"Analyzed {len(self.questions)} questions.")
        self._refresh_table()
        self.btn_fill.setEnabled(True)
        self.lbl_progress.setText("Analysis complete — review, then Fill Form")

    # ---------- table ----------
    def _status_of(self, qid: int) -> str:
        r = self.results.get(qid)
        return r.status if r else self.plans[qid].status

    def _refresh_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.questions))
        for row, q in enumerate(self.questions):
            self._fill_row(row, q)
        self.table.blockSignals(False)
        self._update_stats()

    def _fill_row(self, row: int, q: Question) -> None:
        p, status = self.plans[q.id], self._status_of(q.id)
        detail = self.results[q.id].detail if q.id in self.results else ""
        values = [q.text + (" *" if q.required else ""), q.kind, p.field or "—",
                  p.answer_text() or ("(none)" if not detail else detail), p.source,
                  f"{p.confidence:.2f}", status]
        color = QColor(AI_COLOR if (p.is_draft and status == ST_ANALYZED) else ROW_COLORS.get(status, "#ffffff"))
        for col, v in enumerate(values):
            item = QTableWidgetItem(v)
            item.setBackground(color); item.setForeground(QColor("#000000"))
            item.setToolTip(p.reason if not detail else f"{p.reason}\n{detail}")
            if col == 0:
                item.setData(Qt.UserRole, q.id)
            self.table.setItem(row, col, item)

    def _update_stats(self) -> None:
        plans = list(self.plans.values())
        ready = [p for p in plans if p.ready_to_fill]
        counts = {
            "Questions detected": len(self.questions),
            "Profile answers": sum(1 for p in ready if p.source in (SOURCE_PROFILE, SOURCE_USER) and p.action == "text"),
            "AI drafts": sum(1 for p in ready if p.source == SOURCE_AI),
            "Choices selected": sum(1 for p in ready if p.action in ("one", "many")),
            "Need your input": sum(1 for p in plans if p.status == ST_REVIEW),
            "Failed fields": sum(1 for r in self.results.values() if r.status == ST_FAILED),
        }
        for k, v in counts.items():
            self.stat_labels[k].setText(f"{k}: {v}")

    def _selected_question(self) -> Question | None:
        rows = {i.row() for i in self.table.selectedItems()}
        return self.questions[min(rows)] if rows else None

    def _on_select(self) -> None:
        q = self._selected_question()
        if q:
            self.review.show_plan(q, self.plans[q.id])

    def _row_of(self, qid: int) -> int:
        return next(i for i, q in enumerate(self.questions) if q.id == qid)

    def _update_row(self, qid: int) -> None:
        self.table.blockSignals(True)
        self._fill_row(self._row_of(qid), self.questions[self._row_of(qid)])
        self.table.blockSignals(False)
        self._update_stats()

    # ---------- review actions ----------
    def _on_confirm(self, qid: int, answer) -> None:
        p, q = self.plans[qid], self.questions[self._row_of(qid)]
        p.answer = answer
        p.action = ("many" if q.kind == "checkbox" else "one") if q.kind in CHOICE_KINDS else "text"
        p.source, p.confidence, p.status, p.is_draft = SOURCE_USER, 1.0, ST_ANALYZED, False
        p.reason = "Confirmed by user"
        self.results.pop(qid, None)
        self._update_row(qid)
        self.log(f"Q{qid} confirmed by user.")

    def _on_skip(self, qid: int) -> None:
        p = self.plans[qid]
        p.action, p.answer, p.status, p.reason = "skip", "", ST_SKIPPED, "Skipped by user"
        self._update_row(qid)

    def _on_regenerate(self, qid: int) -> None:
        q = self.questions[self._row_of(qid)]
        st = self.ollama.check()
        if not st.ok:
            QMessageBox.warning(self, "Ollama", st.message); return
        planner = AnswerPlanner(self.profile, self.ollama, self.config)

        def task(w: TaskWorker):
            return planner.regenerate_draft(q)

        def done(new_plan: AnswerPlan) -> None:
            self.plans[qid] = new_plan
            self._update_row(qid); self.review.show_plan(q, new_plan)
            self.log(f"Q{qid}: new AI draft generated.")

        self._start(task, done, "Regenerating draft…")

    # ---------- fill ----------
    def fill(self) -> None:
        todo = [p for p in self.plans.values() if p.ready_to_fill]
        if not todo:
            QMessageBox.information(self, "Nothing to fill", "No question has a confirmed, ready answer."); return
        drafts = sum(1 for p in todo if p.source == SOURCE_AI)
        waiting = sum(1 for p in self.plans.values() if p.status == ST_REVIEW)
        text = (f"Fill {len(todo)} fields in Brave?\n\n• {drafts} are AI drafts (highlighted yellow)\n"
                f"• {waiting} need your input and will be left blank\n\n"
                "The form will NOT be submitted — you submit it yourself.")
        if QMessageBox.question(self, "Approve fill", text) != QMessageBox.Yes:
            return
        questions, plans = list(self.questions), dict(self.plans)

        def task(w: TaskWorker):
            return self.browser.fill(questions, plans, w.cancel_event, w.item.emit)

        def on_item(res: FillResult) -> None:
            self.results[res.qid] = res
            self._update_row(res.qid)
            self.log(f"Q{res.qid}: {res.status} — {res.detail}")

        def on_done(results) -> None:
            c = {s: sum(1 for r in results if r.status == s) for s in (ST_VERIFIED, ST_FILLED, ST_FAILED)}
            skipped = len(self.questions) - len(results)
            summary = (f"Verified {c[ST_VERIFIED]}, filled-unverified {c[ST_FILLED]}, failed {c[ST_FAILED]}, "
                       f"left for you {skipped}.")
            self.log("FILL COMPLETE — " + summary + " Review the form in Brave and submit it yourself.")
            notify("SmartForm AI — review required", summary, bool(self.config.get("enable_notifications", True)))
            self.lbl_progress.setText("Done — review in Brave, then submit manually")

        self._start(task, on_done, "Filling form…", on_item=on_item)

    def stop(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_cancel()
            self.lbl_progress.setText("Stopping…")

    def closeEvent(self, event) -> None:
        self.timer.stop()
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_cancel(); self.worker.wait(3000)
        if self._status_worker is not None:
            self._status_worker.wait(3000)
        try:
            self.browser.shutdown()
        except Exception:
            pass
        super().closeEvent(event)
