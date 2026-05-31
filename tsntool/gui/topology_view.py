"""A QGraphicsView that draws a parsed :class:`~tsntool.topology.Topology`.

Nodes are placed at their NED ``@display`` coordinates, coloured by role, with
the network's background image (e.g. the car) faded behind them. Links are
styled by bitrate. Clicking a node emits :attr:`TopologyView.node_selected`.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsScene, QGraphicsView,
)

from ..topology import Topology

ROLE_COLORS = {
    "switch": "#3b7dd8",
    "device": "#3aaf6a",
    "clock": "#e0922f",
}
ROLE_RADIUS = {"switch": 15, "device": 11, "clock": 13}


class _NodeItem(QGraphicsEllipseItem):
    def __init__(self, node):
        r = ROLE_RADIUS.get(node.role, 11)
        super().__init__(-r, -r, 2 * r, 2 * r)
        self.node = node
        self.setPos(node.x, node.y)
        self.setBrush(QBrush(QColor(ROLE_COLORS.get(node.role, "#888"))))
        self.setPen(QPen(QColor("#1c1c1c"), 1.5))
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setZValue(10)
        self.setToolTip(f"{node.name}  ({node.role})")


class TopologyView(QGraphicsView):
    node_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setBackgroundBrush(QColor("#fafafa"))
        self._scene.selectionChanged.connect(self._on_selection)
        self._topo: Topology | None = None

    def set_topology(self, topo: Topology) -> None:
        self._topo = topo
        self._scene.clear()

        if topo.bg_image and topo.bg_image.is_file():
            pix = QPixmap(str(topo.bg_image))
            if not pix.isNull():
                w = int(topo.width) or pix.width()
                h = int(topo.height) or pix.height()
                item = self._scene.addPixmap(
                    pix.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                item.setZValue(-10)
                item.setOpacity(0.45)

        for link in topo.links:
            s = topo.node(link.src)
            d = topo.node(link.dst)
            if not s or not d:
                continue
            gigabit = "1 gbps" in link.bitrate.lower() or "10 gbps" in link.bitrate.lower()
            pen = QPen(QColor("#2c5fa8" if gigabit else "#999999"))
            pen.setWidthF(2.6 if gigabit else 1.2)
            line = self._scene.addLine(s.x, s.y, d.x, d.y, pen)
            line.setZValue(0)
            line.setToolTip(f"{link.src} ↔ {link.dst}  [{link.bitrate}]")

        label_font = QFont()
        label_font.setPointSize(8)
        for node in topo.nodes:
            self._scene.addItem(_NodeItem(node))
            txt = self._scene.addSimpleText(node.name, label_font)
            txt.setZValue(11)
            txt.setBrush(QBrush(QColor("#222")))
            br = txt.boundingRect()
            r = ROLE_RADIUS.get(node.role, 11)
            txt.setPos(node.x - br.width() / 2, node.y + r + 1)

        rect = self._scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)
        self._scene.setSceneRect(rect)
        self.resetTransform()
        self.fitInView(rect, Qt.KeepAspectRatio)

    # --- interaction -------------------------------------------------------
    def _on_selection(self) -> None:
        for item in self._scene.selectedItems():
            if isinstance(item, _NodeItem):
                self.node_selected.emit(item.node.name)
                return

    def wheelEvent(self, event) -> None:  # zoom
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def reset_zoom(self) -> None:
        if self._topo is not None:
            self.resetTransform()
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)


def render_topology_png(topo: Topology, path) -> str:
    """Render *topo* to a PNG file (headless-friendly). Returns the path."""
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    view = TopologyView()
    view.set_topology(topo)
    scene = view._scene
    rect = scene.sceneRect()
    img = QImage(max(1, int(rect.width())), max(1, int(rect.height())), QImage.Format_ARGB32)
    img.fill(Qt.white)
    painter = QPainter(img)
    scene.render(painter, target=img.rect(), source=rect)
    painter.end()
    img.save(str(path))
    return str(path)
