"""AI settings dialog — make the LLM provider/model/key/URL explicit and editable."""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from .. import ai
from ..settings import Settings

_PROVIDERS = [("Auto-detect", "auto"),
              ("Anthropic (Claude)", "anthropic"),
              ("Ollama (local)", "ollama")]


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._loaded_key = settings.get_api_key()
        self.setWindowTitle("AI settings")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)

        form = QFormLayout()
        self.provider_combo = QComboBox()
        for label, val in _PROVIDERS:
            self.provider_combo.addItem(label, val)
        self.provider_combo.setCurrentIndex(max(0, self.provider_combo.findData(settings.provider or "auto")))
        self.provider_combo.currentIndexChanged.connect(self._sync_enabled)
        form.addRow("Provider:", self.provider_combo)
        lay.addLayout(form)

        # --- Anthropic ---
        self.agrp = QGroupBox("Anthropic (Claude)")
        af = QFormLayout(self.agrp)
        self.anthropic_model = QLineEdit(settings.anthropic_model)
        self.anthropic_model.setPlaceholderText(ai.DEFAULT_ANTHROPIC_MODEL)
        af.addRow("Model:", self.anthropic_model)
        keyrow = QHBoxLayout()
        self.api_key = QLineEdit(self._loaded_key)
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("sk-ant-…  (blank → use ANTHROPIC_API_KEY env var)")
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.api_key.setEchoMode(
            QLineEdit.Normal if on else QLineEdit.Password))
        keyrow.addWidget(self.api_key, 1)
        keyrow.addWidget(show)
        kw = QWidget()
        kw.setLayout(keyrow)
        af.addRow("API key:", kw)
        self.key_loc = QLabel(f"stored in: {settings.key_location()}")
        self.key_loc.setStyleSheet("color:#888; font-size:11px;")
        af.addRow("", self.key_loc)
        lay.addWidget(self.agrp)

        # --- Ollama ---
        self.ogrp = QGroupBox("Ollama (local)")
        of = QFormLayout(self.ogrp)
        self.ollama_url = QLineEdit(settings.ollama_base_url)
        self.ollama_url.setPlaceholderText(ai.OLLAMA_BASE)
        of.addRow("Base URL:", self.ollama_url)
        self.ollama_model = QLineEdit(settings.ollama_model)
        self.ollama_model.setPlaceholderText(ai.DEFAULT_OLLAMA_MODEL)
        of.addRow("Model:", self.ollama_model)
        list_btn = QPushButton("List installed models")
        list_btn.clicked.connect(self._list_ollama)
        of.addRow("", list_btn)
        self.ollama_models_lbl = QLabel("")
        self.ollama_models_lbl.setWordWrap(True)
        self.ollama_models_lbl.setStyleSheet("color:#666; font-size:11px;")
        of.addRow("", self.ollama_models_lbl)
        lay.addWidget(self.ogrp)

        # --- test + buttons ---
        testrow = QHBoxLayout()
        test_btn = QPushButton("Test connection")
        test_btn.clicked.connect(self._test)
        self.test_lbl = QLabel("")
        self.test_lbl.setWordWrap(True)
        testrow.addWidget(test_btn)
        testrow.addWidget(self.test_lbl, 1)
        lay.addLayout(testrow)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self._sync_enabled()

    # --- helpers -----------------------------------------------------------
    def _provider(self) -> str:
        return self.provider_combo.currentData()

    def _sync_enabled(self) -> None:
        p = self._provider()
        self.agrp.setEnabled(p in ("auto", "anthropic"))
        self.ogrp.setEnabled(p in ("auto", "ollama"))

    def _list_ollama(self) -> None:
        base = self.ollama_url.text().strip() or ai.OLLAMA_BASE
        models = ai.list_ollama_models(base)
        self.ollama_models_lbl.setText(
            "installed: " + (", ".join(models) if models else f"(none / unreachable at {base})"))

    def _test(self) -> None:
        p = self._provider()
        field_key = self.api_key.text().strip()
        self.test_lbl.setText("testing…")
        self.test_lbl.setStyleSheet("color:#888;")
        QApplication.processEvents()
        use_anthropic = p == "anthropic" or (
            p == "auto" and (field_key or os.environ.get("ANTHROPIC_API_KEY")))
        if use_anthropic:
            ok, msg = ai.test_anthropic(
                self.anthropic_model.text().strip() or ai.DEFAULT_ANTHROPIC_MODEL,
                field_key or None)
        else:
            base = self.ollama_url.text().strip() or ai.OLLAMA_BASE
            models = ai.list_ollama_models(base)
            ok = bool(models)
            msg = (f"reachable — {len(models)} model(s)" if ok
                   else f"no models / unreachable at {base}")
        self.test_lbl.setText(("✓ " if ok else "✗ ") + msg)
        self.test_lbl.setStyleSheet("color:%s;" % ("#176d2c" if ok else "#a11111"))

    def _on_save(self) -> None:
        self.settings.provider = self._provider()
        self.settings.anthropic_model = self.anthropic_model.text().strip()
        self.settings.ollama_model = self.ollama_model.text().strip()
        self.settings.ollama_base_url = self.ollama_url.text().strip()
        if self.api_key.text().strip() != (self._loaded_key or ""):
            self.settings.set_api_key(self.api_key.text())
        self.settings.save()
        self.accept()
