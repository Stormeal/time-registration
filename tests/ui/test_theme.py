"""Brand/theme transitions preserve the existing functional UI."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QPushButton

from qi_flow.ui.main_window import MainWindow
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog
from qi_flow.ui.theme import ThemeManager
from qi_flow.ui.today_page import TodayPage


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_default_and_primary_buttons_render_as_orange_outline_and_fill(qtbot, qapp, theme):
    manager = ThemeManager(qapp)
    manager.apply(theme)
    alternative = QPushButton("Alternative")
    primary = QPushButton("Primary")
    primary.setProperty("role", "primary")
    for button in (alternative, primary):
        qtbot.addWidget(button)
        button.show()

    outlined = alternative.grab().toImage()
    filled = primary.grab().toImage()
    outline_color = outlined.pixelColor(outlined.width() // 2, 0)
    alternative_center = outlined.pixelColor(outlined.width() // 2, outlined.height() // 2)
    primary_center = filled.pixelColor(filled.width() // 2, filled.height() // 2)

    assert outline_color.red() > outline_color.green() > outline_color.blue()
    assert alternative_center != primary_center
    assert primary_center.red() > primary_center.green() > primary_center.blue()


def test_saved_theme_changes_logo_without_recreating_pages(qtbot, rig):
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    today = window.findChild(TodayPage)
    assert hasattr(window, "_theme_manager"), "Window must own live theme updates"
    manager = window._theme_manager
    manager.apply("light")
    light_logo = window._logo.pixmap().toImage()
    settings = window._settings_page
    settings._theme.setCurrentIndex(settings._theme.findData("dark"))
    settings._save_preferences()
    assert manager.resolved_theme == "dark"
    assert window._logo.pixmap().toImage() != light_logo
    assert window.findChild(TodayPage) is today


def test_system_theme_tracks_colour_scheme_but_override_does_not(qtbot, qapp, rig):
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    assert hasattr(window, "_theme_manager")
    manager = window._theme_manager
    manager.apply("system")
    qapp.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    assert manager.resolved_theme == "dark"
    manager.apply("light")
    qapp.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    assert manager.resolved_theme == "light"


def test_theme_change_preserves_unsaved_note_and_open_editor(qtbot, rig):
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    today = window.findChild(TodayPage)
    today._note.setPlainText("Unsaved: æøå")
    editor = ManualEntryDialog(rig.service)
    qtbot.addWidget(editor)
    editor.show()
    assert hasattr(window, "_theme_manager")
    window._theme_manager.apply("dark")
    assert today._note.toPlainText() == "Unsaved: æøå"
    assert editor.isVisible()
    from qi_flow.ui.theme import logo_path

    assert not QPixmap(str(logo_path("dark"))).isNull()
    assert not QPixmap(str(logo_path("light"))).isNull()
