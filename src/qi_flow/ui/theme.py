"""Shared TestHuset appearance for Qt widgets and live theme changes."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


def logo_path(theme: str) -> Path:
    name = "logo_alternative.png" if theme == "dark" else "logo_primary.png"
    return Path(str(files("qi_flow.assets").joinpath(name)))


class ThemeManager(QObject):
    """One window-owned manager; changes appearance without rebuilding widgets."""

    changed = Signal(str)

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._preference = "system"
        self.resolved_theme = "light"
        app.styleHints().colorSchemeChanged.connect(self._system_changed)

    def apply(self, preference: str) -> None:
        if preference not in ("system", "light", "dark"):
            raise ValueError("Unknown theme preference.")
        self._preference = preference
        scheme = self._app.styleHints().colorScheme()
        self._set_theme(
            "dark"
            if preference == "dark" or (preference == "system" and scheme == Qt.ColorScheme.Dark)
            else "light"
        )

    def _system_changed(self, scheme: Qt.ColorScheme) -> None:
        if self._preference == "system":
            self._set_theme("dark" if scheme == Qt.ColorScheme.Dark else "light")

    def _set_theme(self, theme: str) -> None:
        dark = theme == "dark"
        bg, surface, text, muted, line, selected = (
            ("#22211e", "#2a2824", "#f6f3ed", "#c0b7a7", "#585249", "#453321")
            if dark
            else ("#faf9f6", "#ffffff", "#292722", "#695e4a", "#d2cdc4", "#fff0dd")
        )
        palette = QPalette()
        for role, color in (
            (QPalette.ColorRole.Window, bg),
            (QPalette.ColorRole.WindowText, text),
            (QPalette.ColorRole.Base, surface),
            (QPalette.ColorRole.AlternateBase, bg),
            (QPalette.ColorRole.Text, text),
            (QPalette.ColorRole.Button, surface),
            (QPalette.ColorRole.ButtonText, text),
            (QPalette.ColorRole.Highlight, selected),
            (QPalette.ColorRole.HighlightedText, text),
            (QPalette.ColorRole.ToolTipBase, surface),
            (QPalette.ColorRole.ToolTipText, text),
            (QPalette.ColorRole.PlaceholderText, muted),
        ):
            palette.setColor(role, QColor(color))
        for role in (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
            QPalette.ColorRole.WindowText,
        ):
            palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(muted))
        self._app.setPalette(palette)
        font = QFont("Segoe UI")
        font.setPointSizeF(10.5)
        self._app.setFont(font)
        self._app.setStyleSheet(f"""
            QWidget {{ color: {text}; }}
            QMainWindow, QDialog, QScrollArea > QWidget > QWidget {{ background: {bg}; }}
            QWidget#applicationHeader {{ background: {surface}; border-bottom: 1px solid {line}; }}
            QLabel[role="muted"] {{ color: {muted}; }}
            QLabel[role="heading"] {{ font-size: 22px; font-weight: 600; }}
            QLabel#netTimer {{ font-size: 40px; font-weight: 600; }}
            QLabel[role="total"] {{ font-size: 20px; font-weight: 600; }}
            QPushButton {{ background: {surface}; border: 1px solid #e98517; border-radius: 5px;
                padding: 7px 12px; min-height: 20px; }}
            QPushButton:hover {{ background: {selected}; border-color: #f48f21; }}
            QPushButton:focus {{ border: 2px solid #b66100; padding: 6px 11px; }}
            QPushButton[role="primary"] {{ background: #f48f21; color: #261c0e;
                border-color: #f48f21; font-weight: 600; }}
            QPushButton[role="primary"]:hover {{ background: #ffa23e; }}
            QPushButton[role="primary"]:focus {{ border-color: #753900; }}
            QPushButton:disabled {{ background: {bg}; color: {muted}; border-color: #a56628; }}
            QLineEdit, QTextEdit, QSpinBox, QComboBox, QDateEdit, QTimeEdit {{
                background: {surface}; border: 1px solid {line}; border-radius: 4px;
                padding: 5px; min-height: 22px; selection-background-color: {selected}; }}
            QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus,
            QDateEdit:focus, QTimeEdit:focus {{ border-color: #b66100; }}
            QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
                width: 20px; height: 14px; border-left: 1px solid {line}; }}
            QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
                width: 20px; height: 14px; border-left: 1px solid {line}; }}
            QGroupBox {{ border: 1px solid {line}; border-radius: 5px;
                margin-top: 18px; padding: 18px 12px 12px; font-weight: 600; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
            QTabBar::tab {{ background: transparent; border: 0; padding: 10px 16px;
                border-bottom: 3px solid transparent; }}
            QTabBar::tab:selected {{ background: {selected}; border-bottom-color: #f48f21; }}
            QTabBar::tab:focus {{ border-bottom-color: #b66100; }}
            QTreeWidget {{ background: {surface}; border: 1px solid {line}; }}
            QTreeWidget::item {{ padding: 7px 4px; border-bottom: 1px solid {line}; }}
            QTreeWidget::item:hover {{ background: {selected}; }}
            QTreeWidget::item:selected {{ background: {selected}; color: {text}; }}
            QHeaderView::section {{ background: {bg}; color: {muted}; padding: 8px 4px;
                border: 0; border-bottom: 1px solid {line}; }}
            QMenu, QToolTip {{ background: {surface}; border: 1px solid {line}; padding: 4px; }}
            QMenu::item:selected {{ background: {selected}; }}
            QScrollArea {{ border: 0; }}
            QFrame#trayPanelFrame {{ background: {bg}; border: 1px solid {line}; }}
        """)
        self.resolved_theme = theme
        self.changed.emit(theme)
