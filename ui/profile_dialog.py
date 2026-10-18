"""View / edit profile.json: table of fields plus a raw-JSON tab (validated before saving)."""
from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QMessageBox, QPlainTextEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from services.profile_service import ProfileService
from services.validation_service import validate_json_text, validate_profile_dict


def _cell_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _parse_cell(text: str):
    t = text.strip()
    if t[:1] in "[{":
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            return text
    return text


class ProfileDialog(QDialog):
    def __init__(self, service: ProfileService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Profile")
        self.resize(680, 520)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        fields = QWidget(); fl = QVBoxLayout(fields)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Field", "Value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 220)
        fl.addWidget(self.table)
        row = QHBoxLayout()
        add, rem = QPushButton("Add custom field"), QPushButton("Remove selected")
        add.clicked.connect(lambda: self._add_row("", ""))
        rem.clicked.connect(self._remove_rows)
        row.addWidget(add); row.addWidget(rem); row.addStretch()
        fl.addLayout(row)
        self.tabs.addTab(fields, "Fields")

        self.raw = QPlainTextEdit()
        self.tabs.addTab(self.raw, "Raw JSON")
        self.tabs.currentChanged.connect(self._sync_tabs)
        self._last_tab = 0

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._load(service.data)

    def _add_row(self, k: str, v: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(k))
        self.table.setItem(r, 1, QTableWidgetItem(v))

    def _remove_rows(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedItems()}, reverse=True):
            self.table.removeRow(r)

    def _load(self, data: dict) -> None:
        self.table.setRowCount(0)
        for k, v in data.items():
            self._add_row(str(k), _cell_text(v))
        self.raw.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))

    def _table_data(self) -> dict:
        out = {}
        for r in range(self.table.rowCount()):
            k = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            v = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            if k:
                out[k] = _parse_cell(v)
        return out

    def _sync_tabs(self, idx: int) -> None:
        if self._last_tab == 0 and idx == 1:
            self.raw.setPlainText(json.dumps(self._table_data(), indent=2, ensure_ascii=False))
        elif self._last_tab == 1 and idx == 0:
            ok, res = validate_json_text(self.raw.toPlainText())
            if not ok:
                QMessageBox.warning(self, "Invalid JSON", str(res))
                self.tabs.blockSignals(True); self.tabs.setCurrentIndex(1); self.tabs.blockSignals(False)
                return
            self._load(res)
        self._last_tab = self.tabs.currentIndex()

    def _save(self) -> None:
        if self.tabs.currentIndex() == 1:
            ok, res = validate_json_text(self.raw.toPlainText())
            if not ok:
                QMessageBox.warning(self, "Invalid JSON", str(res)); return
            data = res
        else:
            data = self._table_data()
        errors = validate_profile_dict(data)
        if errors:
            QMessageBox.warning(self, "Invalid profile", "\n".join(errors)); return
        try:
            self.service.save(data)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc)); return
        self.accept()
