"""UI lifecycle tests for the stable desktop shell."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from qi_flow.ui.main_window import MainWindow


def test_closing_main_window_hides_it_for_tray_lifecycle(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    window.close()

    assert not window.isVisible()


def test_show_timesheet_reveals_the_window_on_the_timesheet_page(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.hide()

    window.show_timesheet()

    assert window.isVisible()
    assert window._navigation.currentRow() == window._page_index["Timesheet"]


def test_show_settings_reveals_the_window_on_the_settings_page(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.hide()

    window.show_settings()

    assert window.isVisible()
    assert window._navigation.currentRow() == window._page_index["Settings"]
