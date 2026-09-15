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
