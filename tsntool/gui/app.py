"""PySide6 GUI — Step 2 (topology view + live run monitor + run controls).

Tabs:
  * Run & Monitor — pick a config, run it (Cmdenv/Qtenv), watch a live
    dashboard (progress, sim time, events, speed, memory) and the raw log,
    and see the produced result files.
  * Topology — the network diagram parsed from the NED (@display positions),
    coloured by role, links styled by bitrate; click a node for details.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ..environment import OmnetppEnv
from ..inifile import parse_configs
from ..mcp_client import MCPClient, MCPError, result_text
from ..runner import RunSpec, SimulationRunner, list_result_files
from ..topology import Topology, find_topology
from .topology_view import ROLE_COLORS, TopologyView

# status-line parsers (Cmdenv express mode)
_RE_PROG = re.compile(r"Event #(\d+)\s+t=([\d.eE+\-]+).*?([\d.]+)% completed")
_RE_SPEED = re.compile(r"ev/sec=([\d.eE+\-]+)\s+simsec/sec=([\d.eE+\-]+)")
_RE_MSG = re.compile(r"present:\s*(\d+)\s+in FES:\s*(\d+)\s+Memory \(RSS\):\s*([\d.]+\s*\w+)")


class MainWindow(QMainWindow):
    def __init__(self, env: OmnetppEnv, ini_path: Path | None):
        super().__init__()
        self.env = env
        self.runner = SimulationRunner(env)
        self.proc: QProcess | None = None
        self.ini_path: Path | None = None
        self.topo: Topology | None = None

        self.setWindowTitle("TSN Tool — Step 2 (topology + run monitor)")
        self.resize(1180, 800)
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

        file_row = QHBoxLayout()
        self.ini_label = QLineEdit(readOnly=True)
        open_btn = QPushButton("Open omnetpp.ini…")
        open_btn.clicked.connect(self.on_open)
        file_row.addWidget(QLabel("Network ini:"))
        file_row.addWidget(self.ini_label, 1)
        file_row.addWidget(open_btn)
        root.addLayout(file_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_run_tab(), "Run && Monitor")
        self.tabs.addTab(self._build_topology_tab(), "Topology")
        root.addWidget(self.tabs, 1)

        self.statusBar().showMessage("Ready.")

    def _build_run_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)

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
        self.run_edit = QLineEdit(placeholderText="all runs (empty) or a run number, e.g. 0")
        form.addRow("Run number:", self.run_edit)
        self.time_edit = QLineEdit(placeholderText="ini default (empty) or e.g. 1ms, 10ms")
        form.addRow("Sim-time-limit:", self.time_edit)
        mcp_row = QHBoxLayout()
        self.mcp_check = QCheckBox("Enable MCP server on port")
        self.mcp_check.setToolTip(
            "Exposes the built-in MCP server for AI/automation tools.\n"
            "Note: in Cmdenv the simulation then WAITS for MCP run commands "
            "instead of running to completion — use 'Query MCP state', or Qtenv."
        )
        self.mcp_port = QSpinBox(minimum=1, maximum=65535, value=8765)
        mcp_row.addWidget(self.mcp_check)
        mcp_row.addWidget(self.mcp_port)
        mcp_row.addStretch(1)
        holder = QWidget()
        holder.setLayout(mcp_row)
        form.addRow("MCP:", holder)
        lay.addWidget(opts)

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
        for b in (self.run_btn, self.stop_btn, self.mcp_btn):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        btn_row.addWidget(self.clear_btn)
        lay.addLayout(btn_row)

        lay.addWidget(self._build_dashboard())

        split = QSplitter(Qt.Vertical)
        self.log = QPlainTextEdit(readOnly=True)
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
        split.setSizes([420, 150])
        lay.addWidget(split, 1)
        return tab

    def _build_dashboard(self) -> QWidget:
        box = QGroupBox("Live monitor")
        grid = QGridLayout(box)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        grid.addWidget(self.progress, 0, 0, 1, 4)
        self._stat_labels: dict[str, QLabel] = {}
        fields = [("simtime", "Sim time"), ("event", "Event #"),
                  ("evsec", "ev/sec"), ("simsec", "simsec/sec"),
                  ("msgs", "Messages (live/FES)"), ("rss", "Memory (RSS)")]
        for i, (key, title) in enumerate(fields):
            r, c = divmod(i, 3)
            cell = QVBoxLayout()
            t = QLabel(title)
            t.setStyleSheet("color:#888; font-size:10px;")
            v = QLabel("—")
            v.setStyleSheet("font-weight:bold;")
            cell.addWidget(t)
            cell.addWidget(v)
            w = QWidget()
            w.setLayout(cell)
            grid.addWidget(w, 1 + r, c)
            self._stat_labels[key] = v
        return box

    def _build_topology_tab(self) -> QWidget:
        tab = QWidget()
        lay = QHBoxLayout(tab)
        self.topo_view = TopologyView()
        self.topo_view.node_selected.connect(self.on_node_selected)
        lay.addWidget(self.topo_view, 1)

        side = QVBoxLayout()
        self.topo_summary = QLabel("—")
        self.topo_summary.setWordWrap(True)
        self.topo_summary.setStyleSheet("font-weight:bold;")
        side.addWidget(self.topo_summary)
        legend = QLabel(
            f"<span style='color:{ROLE_COLORS['switch']}'>●</span> switch &nbsp; "
            f"<span style='color:{ROLE_COLORS['device']}'>●</span> device &nbsp; "
            f"<span style='color:{ROLE_COLORS['clock']}'>●</span> clock<br>"
            "<b>—</b> 1/10 Gbps &nbsp; <span style='color:#999'>—</span> 100 Mbps"
        )
        side.addWidget(legend)
        side.addWidget(QLabel("Selected node:"))
        self.node_info = QTextEdit(readOnly=True)
        self.node_info.setMaximumWidth(280)
        side.addWidget(self.node_info, 1)
        reset_btn = QPushButton("Reset zoom")
        reset_btn.clicked.connect(lambda: self.topo_view.reset_zoom())
        side.addWidget(reset_btn)
        hint = QLabel("Scroll to zoom, drag to pan.")
        hint.setStyleSheet("color:#999; font-size:10px;")
        side.addWidget(hint)
        holder = QWidget()
        holder.setLayout(side)
        holder.setFixedWidth(300)
        lay.addWidget(holder)
        return tab

    # --- model -------------------------------------------------------------
    def set_ini(self, path: Path) -> None:
        self.ini_path = Path(path)
        self.ini_label.setText(str(self.ini_path))
        self.config_combo.clear()
        if not self.ini_path.is_file():
            self.statusBar().showMessage(f"ini not found: {self.ini_path}")
            return
        configs = parse_configs(self.ini_path)
        network = next((c.network for c in configs if c.network), None)
        for c in configs:
            suffix = "" if c.runnable else "  [abstract]"
            self.config_combo.addItem(c.name + suffix, userData=c)
            if not c.runnable:
                self.config_combo.model().item(self.config_combo.count() - 1).setEnabled(False)
        for i in range(self.config_combo.count()):
            c = self.config_combo.itemData(i)
            if c and c.runnable:
                self.config_combo.setCurrentIndex(i)
                break
        self.refresh_results()
        self._load_topology(network)
        self.statusBar().showMessage(f"Loaded {self.ini_path.name}")

    def _load_topology(self, network: str | None) -> None:
        self.topo = None
        self.node_info.clear()
        try:
            self.topo = find_topology(self.ini_path, self.env, network)
        except Exception as exc:  # never let topology break the tool
            self.topo_summary.setText(f"topology parse failed: {exc}")
            return
        if self.topo is None:
            self.topo_summary.setText("No NED topology found next to this ini.")
            return
        self.topo_view.set_topology(self.topo)
        self.topo_summary.setText(self.topo.summary())

    def _update_desc(self) -> None:
        c = self.config_combo.currentData()
        self.desc_label.setText(c.description if c else "")

    def current_config_name(self) -> str | None:
        c = self.config_combo.currentData()
        return c.name if c else None

    def _make_spec(self) -> RunSpec | None:
        if not self.ini_path or not self.current_config_name():
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
            ini_path=self.ini_path, config=self.current_config_name(), run=run, ui=ui,
            sim_time_limit=self.time_edit.text().strip() or None,
            status_frequency="0.2s", mcp_address=mcp,
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
        self._reset_dashboard()
        self._append(f"$ {self.runner.preview(spec)}\n")
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._on_proc_output)
        self.proc.finished.connect(self._on_proc_finished)
        cmd = self.runner.build_bash(spec)
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

    def on_node_selected(self, name: str) -> None:
        if not self.topo:
            return
        node = self.topo.node(name)
        if not node:
            return
        neighbors = []
        for link in self.topo.links:
            if link.src == name:
                neighbors.append((link.dst, link.bitrate))
            elif link.dst == name:
                neighbors.append((link.src, link.bitrate))
        rows = "".join(f"<li>{p} <i>[{br}]</i></li>" for p, br in neighbors) or "<li>(none)</li>"
        self.node_info.setHtml(
            f"<b>{node.name}</b><br>role: {node.role}<br>"
            f"ports/links: {len(neighbors)}<br>position: ({node.x:.0f}, {node.y:.0f})"
            f"<br><br><u>Connected to:</u><ul>{rows}</ul>"
        )

    # --- process callbacks -------------------------------------------------
    def _on_proc_output(self) -> None:
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._append(data)
        self._parse_status(data)

    def _on_proc_finished(self, code: int, _status) -> None:
        self._append(f"\n# finished, exit code {code}\n")
        if code == 0:
            self.progress.setValue(100)
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.statusBar().showMessage(f"Finished (exit {code}).")
        self.refresh_results()

    # --- helpers -----------------------------------------------------------
    def _parse_status(self, chunk: str) -> None:
        m = _RE_PROG.search(chunk)
        if m:
            self._stat_labels["event"].setText(m.group(1))
            self._stat_labels["simtime"].setText(f"{m.group(2)} s")
            try:
                self.progress.setValue(int(float(m.group(3))))
            except ValueError:
                pass
        m = _RE_SPEED.search(chunk)
        if m:
            self._stat_labels["evsec"].setText(m.group(1))
            self._stat_labels["simsec"].setText(m.group(2))
        m = _RE_MSG.search(chunk)
        if m:
            self._stat_labels["msgs"].setText(f"{m.group(1)} / {m.group(2)}")
            self._stat_labels["rss"].setText(m.group(3))

    def _reset_dashboard(self) -> None:
        self.progress.setValue(0)
        for lbl in self._stat_labels.values():
            lbl.setText("—")

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
