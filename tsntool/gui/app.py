"""PySide6 GUI — Step 2 (topology view + live run monitor + run controls).

Tabs:
  * Run & Monitor — pick a config, run it (Cmdenv/Qtenv), watch a live
    dashboard (progress, sim time, events, speed, memory) and the raw log,
    and see the produced result files.
  * Topology — the network diagram parsed from the NED (@display positions),
    coloured by role, links styled by bitrate; click a node for details.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QMainWindow, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ..environment import OmnetppEnv
from ..inifgen import (
    FEATURES, SCHEDULING_CHOICES, TRISTATE, ConfigSpec, TrafficClass,
    compatibility_warnings, generate_config_text, write_generated_ini,
)
from ..inifile import parse_configs, runnable_configs
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
        self._loading = True  # suppress config-preview refresh during construction

        self.setWindowTitle("TSN Tool — Step 2 (topology + config builder + run monitor)")
        self.resize(1180, 820)
        self._build_ui()
        self.set_ini(ini_path or env.default_showcase_ini)
        self._loading = False
        self._refresh_config()

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
        self.tabs.addTab(self._build_config_tab(), "Configure (ZeroConfigTSN)")
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
        self.monitor_note = QLabel("")
        self.monitor_note.setWordWrap(True)
        self.monitor_note.setStyleSheet("color:#b8860b;")  # amber; shown only when relevant
        self.monitor_note.setVisible(False)
        grid.addWidget(self.monitor_note, 0, 0, 1, 4)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        grid.addWidget(self.progress, 1, 0, 1, 4)
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
            grid.addWidget(w, 2 + r, c)
            self._stat_labels[key] = v
        return box

    def _build_config_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        intro = QLabel(
            "Build a runnable TSN configuration by extending a working base and "
            "overriding feature toggles + per-class shaping. The result is written "
            "to a separate <i>tsntool_generated.ini</i> (the showcase is never modified)."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#555;")
        outer.addWidget(intro)

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        form = QFormLayout()
        self.base_combo = QComboBox()
        self.base_combo.currentIndexChanged.connect(self._refresh_config)
        form.addRow("Base config (extends):", self.base_combo)
        self.cfgname_edit = QLineEdit("TsnTool_Custom")
        self.cfgname_edit.textChanged.connect(self._refresh_config)
        form.addRow("New config name:", self.cfgname_edit)
        ll.addLayout(form)

        feat_box = QGroupBox("TSN features (override on switches)")
        fform = QFormLayout(feat_box)
        self.feature_combos: dict[str, QComboBox] = {}
        for key, label, _pat in FEATURES:
            cb = QComboBox()
            cb.addItems(TRISTATE)
            cb.currentIndexChanged.connect(self._refresh_config)
            self.feature_combos[key] = cb
            fform.addRow(label + ":", cb)
        ll.addWidget(feat_box)

        cls_box = QGroupBox("Per-traffic-class shaping")
        cls_l = QVBoxLayout(cls_box)
        self.class_table = QTableWidget(0, 6)
        self.class_table.setHorizontalHeaderLabels(
            ["Name", "Index", "Scheduling", "CBS idleSlope", "TAS open µs", "TAS cycle µs"])
        self.class_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.class_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.class_table.itemChanged.connect(self._refresh_config)
        cls_l.addWidget(self.class_table)
        row_btns = QHBoxLayout()
        add_btn = QPushButton("Add class")
        add_btn.clicked.connect(lambda: self._add_class_row())
        del_btn = QPushButton("Remove selected")
        del_btn.clicked.connect(self._remove_class_row)
        row_btns.addWidget(add_btn)
        row_btns.addWidget(del_btn)
        row_btns.addStretch(1)
        cls_l.addLayout(row_btns)
        ll.addWidget(cls_box, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("Compatibility warnings:"))
        self.warn_box = QTextEdit(readOnly=True)
        self.warn_box.setMaximumHeight(150)
        rl.addWidget(self.warn_box)
        rl.addWidget(QLabel("Generated config preview:"))
        self.preview = QPlainTextEdit(readOnly=True)
        self.preview.setFont(QFont("Menlo", 11))
        rl.addWidget(self.preview, 1)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.gen_run_btn = QPushButton("Generate, save && run ▶")
        self.gen_run_btn.clicked.connect(self.on_save_and_run)
        btns.addWidget(self.gen_run_btn)
        rl.addLayout(btns)
        split.addWidget(right)
        split.setSizes([560, 520])
        outer.addWidget(split, 1)

        self._seed_class_table()
        return tab

    # --- config-builder helpers -------------------------------------------
    def _add_class_row(self, name="", index=0, sched="CBS", idle="40Mbps",
                       open_us="100", cycle_us="1000") -> None:
        prev = self._loading
        self._loading = True
        r = self.class_table.rowCount()
        self.class_table.insertRow(r)
        self.class_table.setItem(r, 0, QTableWidgetItem(name))
        self.class_table.setItem(r, 1, QTableWidgetItem(str(index)))
        combo = QComboBox()
        combo.addItems(SCHEDULING_CHOICES)
        combo.setCurrentText(sched)
        combo.currentIndexChanged.connect(self._refresh_config)
        self.class_table.setCellWidget(r, 2, combo)
        self.class_table.setItem(r, 3, QTableWidgetItem(idle))
        self.class_table.setItem(r, 4, QTableWidgetItem(str(open_us)))
        self.class_table.setItem(r, 5, QTableWidgetItem(str(cycle_us)))
        self._loading = prev
        self._refresh_config()

    def _remove_class_row(self) -> None:
        rows = sorted({i.row() for i in self.class_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.class_table.removeRow(r)
        self._refresh_config()

    def _seed_class_table(self) -> None:
        # AVB classes mirroring the in-vehicle ManualTsn CBS setup
        self._add_class_row("CDT (control)", 6, "CBS", "10Mbps")
        self._add_class_row("Class A (video/lidar)", 5, "CBS", "40Mbps")
        self._add_class_row("Class B (infotainment)", 4, "CBS", "40Mbps")

    def _collect_spec(self) -> ConfigSpec:
        features = {k: cb.currentText() for k, cb in self.feature_combos.items()}
        classes: list[TrafficClass] = []
        for r in range(self.class_table.rowCount()):
            def cell(c, default=""):
                it = self.class_table.item(r, c)
                return it.text().strip() if it and it.text().strip() else default
            try:
                index = int(cell(1, "0"))
            except ValueError:
                index = 0
            try:
                open_us = float(cell(4, "100"))
            except ValueError:
                open_us = 100.0
            try:
                cycle_us = float(cell(5, "1000"))
            except ValueError:
                cycle_us = 1000.0
            combo = self.class_table.cellWidget(r, 2)
            classes.append(TrafficClass(
                name=cell(0, f"class{r}"), index=index,
                scheduling=combo.currentText() if combo else "inherit",
                idle_slope=cell(3, "40Mbps"), tas_open_us=open_us, tas_cycle_us=cycle_us,
            ))
        return ConfigSpec(
            name=self.cfgname_edit.text().strip() or "TsnTool_Custom",
            base=self.base_combo.currentText(),
            features=features, classes=classes,
        )

    def _refresh_config(self) -> None:
        if getattr(self, "_loading", True):
            return
        spec = self._collect_spec()
        self.preview.setPlainText(generate_config_text(spec))
        warns = compatibility_warnings(spec)
        self.warn_box.setPlainText("\n".join("• " + w for w in warns)
                                   if warns else "No issues detected.")

    def on_save_and_run(self) -> None:
        if not self.ini_path:
            return
        spec = self._collect_spec()
        try:
            out = write_generated_ini(spec, self.ini_path)
        except Exception as exc:
            self._append(f"error writing generated ini: {exc}\n")
            return
        self._append(f"# wrote generated config to {out}\n")
        ui = "Qtenv" if self.ui_combo.currentIndex() == 1 else "Cmdenv"
        mcp = f"localhost:{self.mcp_port.value()}" if self.mcp_check.isChecked() else None
        run = RunSpec(ini_path=out, config=spec.name, ui=ui,
                      sim_time_limit=self.time_edit.text().strip() or None,
                      status_frequency="0.2s", mcp_address=mcp)
        self.tabs.setCurrentIndex(0)  # Run & Monitor
        self._start_run(run)

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

        # populate the config-builder base dropdown (prefer a rich TSN base)
        prev_loading = self._loading
        self._loading = True
        self.base_combo.clear()
        self.base_combo.addItems([c.name for c in runnable_configs(self.ini_path)])
        for pref in ("ManualTsn", "AutomaticTsn"):
            idx = self.base_combo.findText(pref)
            if idx >= 0:
                self.base_combo.setCurrentIndex(idx)
                break
        self._loading = prev_loading

        self.refresh_results()
        self._load_topology(network)
        self._refresh_config()
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
        self._start_run(self._make_spec())

    def _start_run(self, spec: RunSpec | None) -> None:
        if spec is None:
            return
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self._append("# a simulation is already running — stop it first\n")
            return
        self._reset_dashboard()
        self._mcp_mode = bool(spec.mcp_address)
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
            txt = result_text(client.get_simulation_state())
            self._append(txt + "\n")
            try:
                self._update_dashboard_from_state(json.loads(txt))
            except (ValueError, TypeError):
                pass
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
        if getattr(self, "_mcp_mode", False) and (
                "Waiting for MCP requests" in data or "MCP server listening" in data):
            self.monitor_note.setText(
                "⚠ MCP server mode: the network is set up and the simulation is WAITING for "
                "MCP/AI commands — it does not advance on its own, so the counters below reflect "
                "setup only (event 0, t=0; the message count is objects created during "
                "initialization). Click ‘Query MCP state’ to inspect it; uncheck ‘Enable MCP "
                "server’ for a normal timed run; or use Qtenv to run interactively with the AI chat.")
            self.monitor_note.setVisible(True)

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
        self.monitor_note.clear()
        self.monitor_note.setVisible(False)

    def _update_dashboard_from_state(self, st: dict) -> None:
        """Update the live counters from an MCP get_simulation_state result."""
        if st.get("eventNumber") is not None:
            self._stat_labels["event"].setText(str(st["eventNumber"]))
        if st.get("simTime") is not None:
            self._stat_labels["simtime"].setText(f"{st['simTime']} s")
        if st.get("state"):
            self.monitor_note.setText(
                f"MCP state: {st['state']} — queried live via MCP. In MCP mode the simulation "
                "advances only on MCP run commands (full MCP-driven runs arrive with the AI step).")
            self.monitor_note.setVisible(True)

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
