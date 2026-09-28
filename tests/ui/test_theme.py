"""Brand/theme transitions preserve the existing functional UI."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from qi_flow.ui.main_window import MainWindow
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog
from qi_flow.ui.today_page import TodayPage


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
