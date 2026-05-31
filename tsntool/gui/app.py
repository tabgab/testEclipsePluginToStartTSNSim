"""Minimal PySide6 GUI — Step 1.

Pick an ``omnetpp.ini``, choose a runnable configuration, run it (headless in
Cmdenv or interactively in Qtenv), watch the live log, and see the produced
result files. Optionally exposes the built-in MCP server and can query a
running simulation's state through it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ..environment import OmnetppEnv
from ..inifile import parse_configs
from ..mcp_client import MCPClient, MCPError, result_text
from ..runner import RunSpec, SimulationRunner, list_result_files


class MainWindow(QMainWindow):
    def __init__(self, env: OmnetppEnv, ini_path: Path | None):
        super().__init__()
        self.env = env
        self.runner = SimulationRunner(env)
        self.proc: QProcess | None = None
        self.ini_path: Path | None = None

        self.setWindowTitle("TSN Tool — Step 1 (run a TSN showcase config)")
        self.resize(1024, 720)
        self._build_ui()

        self.set_ini(ini_path or env.default_showcase_ini)

    # --- UI construction ---------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        header = QLabel(self.env.describe())
        header.setStyleSheet("color:#444; font-family:monospace;")
        header.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(header)

        # ini file row
        file_row = QHBoxLayout()
        self.ini_label = QLineEdit()
        self.ini_label.setReadOnly(True)
        open_btn = QPushButton("Open omnetpp.ini…")
        open_btn.clicked.connect(self.on_open)
        file_row.addWidget(QLabel("Network ini:"))
        file_row.addWidget(self.ini_label, 1)
        file_row.addWidget(open_btn)
        root.addLayout(file_row)

        # configuration + options
        opts = QGroupBox("Run configuration")
        form = QFormLayout(opts)
        self.config_combo = QComboBox()
        self.config_combo.currentIndexChanged.connect(self._update_desc)
        form.addRow("Configuration:", self.config_combo)
        self.desc_label = QLabel("")
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet("color:#666;")
        form.addRow("", self.desc_label)

        self.ui_combo = QComboBox()
        self.ui_combo.addItems(["Cmdenv (headless)", "Qtenv (GUI)"])
        form.addRow("Environment:", self.ui_combo)

        self.run_edit = QLineEdit()
        self.run_edit.setPlaceholderText("all runs (leave empty), or a run number e.g. 0")
        form.addRow("Run number:", self.run_edit)

        self.time_edit = QLineEdit()
        self.time_edit.setPlaceholderText("use ini default (leave empty), or e.g. 1ms")
        form.addRow("Sim-time-limit:", self.time_edit)

        mcp_row = QHBoxLayout()
        self.mcp_check = QCheckBox("Enable MCP server on port")
        self.mcp_check.setToolTip(
            "Exposes the built-in MCP server for AI/automation tools.\n"
            "Note: in Cmdenv the simulation then WAITS for MCP run commands "
            "instead of running to completion — use 'Query MCP state', or "
            "Qtenv, when this is enabled."
        )
        self.mcp_port = QSpinBox()
        self.mcp_port.setRange(1, 65535)
        self.mcp_port.setValue(8765)
        mcp_row.addWidget(self.mcp_check)
        mcp_row.addWidget(self.mcp_port)
        mcp_row.addStretch(1)
        mcp_holder = QWidget()
        mcp_holder.setLayout(mcp_row)
        form.addRow("MCP:", mcp_holder)

        root.addWidget(opts)

        # action buttons
        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("▶ Run")
        self.run_btn.clicked.connect(self.on_run)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.setEnabled(False)
        self.mcp_btn = QPushButton("Query MCP state")
        self.mcp_btn.clicked.connect(self.on_query_mcp)
        self.clear_btn = QPushButton("Clear log")
        self.clear_btn.clicked.connect(lambda: self.log.clear())
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.mcp_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.clear_btn)
        root.addLayout(btn_row)

        # log + results split
        split = QSplitter(Qt.Vertical)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Menlo", 11))
        log_box = QGroupBox("Simulation output")
        QVBoxLayout(log_box).addWidget(self.log)
        split.addWidget(log_box)

        res_box = QGroupBox("Result files")
        res_layout = QVBoxLayout(res_box)
        self.results = QListWidget()
        refresh_btn = QPushButton("Refresh result files")
        refresh_btn.clicked.connect(self.refresh_results)
        res_layout.addWidget(self.results)
        res_layout.addWidget(refresh_btn)
        split.addWidget(res_box)
        split.setSizes([460, 180])
        root.addWidget(split, 1)

        self.statusBar().showMessage("Ready.")

    # --- model -------------------------------------------------------------
    def set_ini(self, path: Path) -> None:
        self.ini_path = Path(path)
        self.ini_label.setText(str(self.ini_path))
        self.config_combo.clear()
        if not self.ini_path.is_file():
            self.statusBar().showMessage(f"ini not found: {self.ini_path}")
            return
        for c in parse_configs(self.ini_path):
            suffix = "" if c.runnable else "  [abstract]"
            self.config_combo.addItem(c.name + suffix, userData=c)
            idx = self.config_combo.count() - 1
            if not c.runnable:
                self.config_combo.model().item(idx).setEnabled(False)
        # select first runnable config
        for i in range(self.config_combo.count()):
            c = self.config_combo.itemData(i)
            if c and c.runnable:
                self.config_combo.setCurrentIndex(i)
                break
        self.refresh_results()
        self.statusBar().showMessage(f"Loaded {self.ini_path.name}")

    def _update_desc(self) -> None:
        c = self.config_combo.currentData()
        self.desc_label.setText(c.description if c else "")

    def current_config_name(self) -> str | None:
        c = self.config_combo.currentData()
        return c.name if c else None

    def _make_spec(self) -> RunSpec | None:
        if not self.ini_path:
            return None
        config = self.current_config_name()
        if not config:
            return None
        run = None
        if self.run_edit.text().strip():
            try:
                run = int(self.run_edit.text().strip())
            except ValueError:
                self._append("error: run number must be an integer\n")
                return None
        ui = "Qtenv" if self.ui_combo.currentIndex() == 1 else "Cmdenv"
        mcp = f"localhost:{self.mcp_port.value()}" if self.mcp_check.isChecked() else None
        return RunSpec(
            ini_path=self.ini_path, config=config, run=run, ui=ui,
            sim_time_limit=self.time_edit.text().strip() or None,
            mcp_address=mcp,
        )

    # --- actions -----------------------------------------------------------
    def on_open(self) -> None:
        start = str(self.ini_path.parent if self.ini_path else self.env.showcase_dir)
        path, _ = QFileDialog.getOpenFileName(self, "Open omnetpp.ini", start,
                                              "OMNeT++ ini (*.ini);;All files (*)")
        if path:
            self.set_ini(Path(path))

    def on_run(self) -> None:
        spec = self._make_spec()
        if spec is None:
            return
        self._append(f"$ {self.runner.preview(spec)}\n")
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._on_proc_output)
        self.proc.finished.connect(self._on_proc_finished)
        cmd = self.runner.build_bash(spec)  # ["bash", "-c", script]
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.statusBar().showMessage(f"Running {spec.config} ({spec.ui})…")
        self.proc.start(cmd[0], cmd[1:])

    def on_stop(self) -> None:
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.proc.terminate()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()

    def on_query_mcp(self) -> None:
        port = self.mcp_port.value()
        self._append(f"\n# querying MCP server at localhost:{port} …\n")
        client = MCPClient(port=port, timeout=8)
        try:
            info = client.initialize()
            server = (info or {}).get("serverInfo", {})
            self._append(f"  connected: {server.get('name', '?')} {server.get('version', '')}\n")
            self._append("  -- get_simulation_state --\n")
            self._append(result_text(client.get_simulation_state()) + "\n")
        except MCPError as exc:
            self._append(f"  MCP error: {exc}\n"
                         "  (start a run with 'Enable MCP server' checked first)\n")

    # --- process callbacks -------------------------------------------------
    def _on_proc_output(self) -> None:
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._append(data)

    def _on_proc_finished(self, code: int, _status) -> None:
        self._append(f"\n# finished, exit code {code}\n")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.statusBar().showMessage(f"Finished (exit {code}).")
        self.refresh_results()

    # --- helpers -----------------------------------------------------------
    def refresh_results(self) -> None:
        self.results.clear()
        if not self.ini_path:
            return
        files = list_result_files(self.ini_path.parent)
        for f in files:
            self.results.addItem(f"{f.name}    ({f.stat().st_size:,} bytes)")
        if not files:
            self.results.addItem("(no result files yet — run a configuration)")

    def _append(self, text: str) -> None:
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QTextCursor.End)


def run_gui(env: OmnetppEnv, ini_path: Path | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow(env, ini_path)
    win.show()
    return app.exec()
