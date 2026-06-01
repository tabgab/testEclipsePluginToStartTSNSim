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
              ("OpenRouter", "openrouter"),
              ("Ollama (local)", "ollama")]


def _key_field(value: str, placeholder: str) -> tuple[QWidget, QLineEdit]:
    """A masked key line-edit with a Show toggle; returns (row widget, line edit)."""
    edit = QLineEdit(value)
    edit.setEchoMode(QLineEdit.Password)
    edit.setPlaceholderText(placeholder)
    show = QCheckBox("Show")
    show.toggled.connect(lambda on: edit.setEchoMode(
        QLineEdit.Normal if on else QLineEdit.Password))
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    row.addWidget(show)
    w = QWidget()
    w.setLayout(row)
    return w, edit


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._loaded_anthropic = settings.get_api_key("anthropic")
        self._loaded_or = settings.get_api_key("openrouter")
        self.setWindowTitle("AI settings")
        self.setMinimumWidth(540)
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
        kw, self.anthropic_key = _key_field(
            self._loaded_anthropic, "sk-ant-…  (blank → ANTHROPIC_API_KEY env var)")
        af.addRow("API key:", kw)
        self.anthropic_loc = QLabel(f"stored in: {settings.key_location('anthropic')}")
        self.anthropic_loc.setStyleSheet("color:#888; font-size:11px;")
        af.addRow("", self.anthropic_loc)
        lay.addWidget(self.agrp)

        # --- OpenRouter ---
        self.orgrp = QGroupBox("OpenRouter")
        orf = QFormLayout(self.orgrp)
        self.or_model = QLineEdit(settings.openrouter_model)
        self.or_model.setPlaceholderText(ai.DEFAULT_OPENROUTER_MODEL
                                         + "  (any OpenRouter model id, e.g. openai/gpt-4o)")
        orf.addRow("Model:", self.or_model)
        kw2, self.or_key = _key_field(
            self._loaded_or, "sk-or-…  (blank → OPENROUTER_API_KEY env var)")
        orf.addRow("API key:", kw2)
        self.or_loc = QLabel(f"stored in: {settings.key_location('openrouter')}")
        self.or_loc.setStyleSheet("color:#888; font-size:11px;")
        orf.addRow("", self.or_loc)
        lay.addWidget(self.orgrp)

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
        self.orgrp.setEnabled(p in ("auto", "openrouter"))
        self.ogrp.setEnabled(p in ("auto", "ollama"))

    def _list_ollama(self) -> None:
        base = self.ollama_url.text().strip() or ai.OLLAMA_BASE
        models = ai.list_ollama_models(base)
        self.ollama_models_lbl.setText(
            "installed: " + (", ".join(models) if models else f"(none / unreachable at {base})"))

    def _test(self) -> None:
        p = self._provider()
        a_key = self.anthropic_key.text().strip()
        or_key = self.or_key.text().strip()
        self.test_lbl.setText("testing…")
        self.test_lbl.setStyleSheet("color:#888;")
        QApplication.processEvents()

        use_anthropic = p == "anthropic" or (
            p == "auto" and (a_key or os.environ.get("ANTHROPIC_API_KEY")))
        use_or = (not use_anthropic) and (p == "openrouter" or (
            p == "auto" and (or_key or os.environ.get("OPENROUTER_API_KEY"))))

        if use_anthropic:
            ok, msg = ai.test_anthropic(
                self.anthropic_model.text().strip() or ai.DEFAULT_ANTHROPIC_MODEL, a_key or None)
        elif use_or:
            ok, msg = ai.test_openrouter(
                self.or_model.text().strip() or ai.DEFAULT_OPENROUTER_MODEL,
                or_key or os.environ.get("OPENROUTER_API_KEY"))
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
        self.settings.openrouter_model = self.or_model.text().strip()
        self.settings.ollama_model = self.ollama_model.text().strip()
        self.settings.ollama_base_url = self.ollama_url.text().strip()
        if self.anthropic_key.text().strip() != (self._loaded_anthropic or ""):
            self.settings.set_api_key(self.anthropic_key.text(), "anthropic")
        if self.or_key.text().strip() != (self._loaded_or or ""):
            self.settings.set_api_key(self.or_key.text(), "openrouter")
        self.settings.save()
        self.accept()
