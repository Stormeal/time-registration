"""Compact navigation retains routes and remains usable in smaller windows."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabBar

from qi_flow.ui.main_window import MainWindow


def test_horizontal_tabs_select_existing_pages(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert isinstance(window._navigation, QTabBar), "Compact shell needs horizontal navigation"
    timesheet = window._pages.widget(window._page_index["Timesheet"])
    qtbot.mouseClick(
        window._navigation, Qt.MouseButton.LeftButton, pos=window._navigation.tabRect(1).center()
    )
    assert window._pages.currentWidget() is timesheet
    window.hide()
    window.show_settings()
    assert window.isVisible()
    assert window._pages.currentIndex() == window._page_index["Settings"]


def test_compact_header_keeps_tabs_reachable_at_narrow_width(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(640, 520)
    window.show()
    assert isinstance(window._navigation, QTabBar)
    assert window.width() == 640
    qtbot.waitUntil(
        lambda: all(
            window._navigation.rect().contains(window._navigation.tabRect(index))
            for index in range(window._navigation.count())
        )
    )
    for index in range(window._navigation.count()):
        assert window._navigation.rect().contains(window._navigation.tabRect(index))
    assert window._logo.isVisible()


def test_long_destination_labels_remain_reachable_in_compact_window(qtbot, rig):
    from qi_flow.domain.models import IsoWeek
    from qi_flow.ui.timesheet_page import TimesheetPage

    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    window.resize(640, 520)
    window.show_timesheet()
    page = window.findChild(TimesheetPage)
    page._testhuset_button.setVisible(True)
    page.layout().addWidget(page._testhuset_button)
    page._show_week(IsoWeek(2026, 40))
    qtbot.waitUntil(lambda: page._testhuset_button.isVisible())
    assert window.width() == 640
    assert page.rect().contains(page._testhuset_button.geometry())
