"""Native controls with page-scrolling and whole-row hover behavior."""

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPointF,
)
from PySide6.QtGui import QMouseEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QComboBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QWidget,
)


class SettingsWheelGuard(QObject):
    """Route wheel input to page scrolling while retaining native keyboard editing."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(watched, (QAbstractSpinBox, QComboBox)):
            assert isinstance(event, QWheelEvent)
            ancestor = watched.parentWidget()
            while ancestor is not None:
                if isinstance(ancestor, QAbstractScrollArea):
                    forwarded = QWheelEvent(
                        QPointF(
                            ancestor.viewport().mapFromGlobal(event.globalPosition().toPoint())
                        ),
                        event.globalPosition(),
                        event.pixelDelta(),
                        event.angleDelta(),
                        event.buttons(),
                        event.modifiers(),
                        event.phase(),
                        event.inverted(),
                    )
                    QCoreApplication.sendEvent(ancestor.viewport(), forwarded)
                    event.accept()
                    return True
                ancestor = ancestor.parentWidget()
            event.ignore()
            return True
        return super().eventFilter(watched, event)


class RowHoverDelegate(QStyledItemDelegate):
    def __init__(self, tree: "RowHoverTree") -> None:
        super().__init__(tree)
        self._tree = tree

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        row_option = QStyleOptionViewItem(option)
        if index.sibling(index.row(), 0) == self._tree.hovered_index:
            row_option.state |= QStyle.StateFlag.State_MouseOver
        else:
            row_option.state &= ~QStyle.StateFlag.State_MouseOver
        super().paint(painter, row_option, index)


class RowHoverTree(QTreeWidget):
    """Track hover by row identity, including when crossing cell boundaries."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hovered_index = QPersistentModelIndex()
        self.setMouseTracking(True)
        self.setItemDelegate(RowHoverDelegate(self))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        index = self.indexAt(event.position().toPoint()).siblingAtColumn(0)
        if index != self.hovered_index:
            self.hovered_index = QPersistentModelIndex(index)
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.hovered_index = QPersistentModelIndex()
        self.viewport().update()
        super().leaveEvent(event)
