"""Edit the ordinary settings in config.json (no source editing needed)."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox


class SettingsDialog(QDialog):
    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.config = config
        form = QFormLayout(self)
        self.model = QLineEdit(str(config.get("ollama_model", "qwen2.5:3b")))
        self.ollama = QLineEdit(str(config.get("ollama_base_url", "http://127.0.0.1:11434")))
        self.cdp = QLineEdit(str(config.get("cdp_url", "http://127.0.0.1:9222")))
        self.timeout = QSpinBox(); self.timeout.setRange(10, 600)
        self.timeout.setValue(int(config.get("request_timeout_seconds", 120)))
        self.mark = QCheckBox("Prefix AI drafts with “[AI DRAFT — REVIEW]” inside the form")
        self.mark.setChecked(bool(config.get("mark_generated_drafts", False)))
        self.notify = QCheckBox("Windows notifications")
        self.notify.setChecked(bool(config.get("enable_notifications", True)))
        for label, w in (("Ollama model", self.model), ("Ollama URL", self.ollama), ("Brave CDP URL", self.cdp),
                         ("AI timeout (s)", self.timeout), ("", self.mark), ("", self.notify)):
            form.addRow(label, w)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)

    def result_config(self) -> dict:
        cfg = dict(self.config)
        cfg.update(ollama_model=self.model.text().strip() or "qwen2.5:3b",
                   ollama_base_url=self.ollama.text().strip(), cdp_url=self.cdp.text().strip(),
                   request_timeout_seconds=self.timeout.value(), mark_generated_drafts=self.mark.isChecked(),
                   enable_notifications=self.notify.isChecked())
        return cfg
