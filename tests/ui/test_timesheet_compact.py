"""Full monthly review survives compact layout and refresh after corrections."""

from datetime import UTC, datetime

from PySide6.QtCore import Qt

from qi_flow.application.dto import ManualWorkSessionCommand
from qi_flow.domain.models import IsoWeek
from qi_flow.ui.timesheet_page import TimesheetPage


def day_item(page, day):
    for index in range(page._tree.topLevelItemCount()):
        group = page._tree.topLevelItem(index)
        for child in range(group.childCount()):
            item = group.child(child)
            if item.data(0, Qt.ItemDataRole.UserRole).work_date.day == day:
                return item
    raise AssertionError("Calendar day missing")


def make_page(qtbot, rig):
    page = TimesheetPage(rig.service)
    qtbot.addWidget(page)
    page._year, page._month = 2026, 9
    page.refresh()
    return page


def test_month_retains_all_days_and_required_columns(qtbot, rig):
    page = make_page(qtbot, rig)
    assert (
        sum(page._tree.topLevelItem(i).childCount() for i in range(page._tree.topLevelItemCount()))
        == 30
    )
    assert page._tree.columnCount() == 9
    assert day_item(page, 27).data(0, Qt.ItemDataRole.UserRole).work_date.weekday() == 6


def test_refresh_sizes_every_column_to_its_contents(qtbot, rig):
    page = make_page(qtbot, rig)
    page.show()
    for column in range(page._tree.columnCount()):
        page._tree.setColumnWidth(column, 1)

    page.refresh()

    assert all(
        page._tree.columnWidth(column) >= page._tree.sizeHintForColumn(column)
        for column in range(page._tree.columnCount())
    )


def test_week_target_override_does_not_change_default(qtbot, rig):
    page = make_page(qtbot, rig)
    page._tree.setCurrentItem(day_item(page, 28))
    page._target_hours.setValue(32)
    assert rig.service.weekly_progress(IsoWeek(2026, 40)).target_minutes == 1920
    assert rig.service.app_preferences().weekly_target_minutes == 2220


def test_edit_return_refreshes_totals_and_preserves_selected_week(qtbot, rig):
    page = make_page(qtbot, rig)
    page.show()
    page._tree.setCurrentItem(day_item(page, 28))
    page.hide()
    rig.service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 28, 6, tzinfo=UTC), datetime(2026, 9, 28, 7, tzinfo=UTC)
        )
    )
    page.show()
    assert day_item(page, 28).text(5) == "01:00", "Returning must query updated records"
    assert page._tree.currentItem().data(0, Qt.ItemDataRole.UserRole).work_date.day == 28
    assert page._selected_week == IsoWeek(2026, 40)


def test_refresh_preserves_group_selection_across_iso_year(qtbot, rig):
    page = make_page(qtbot, rig)
    page._year, page._month = 2027, 1
    page.refresh()
    page._tree.setCurrentItem(day_item(page, 1).parent())
    assert page._selected_week == IsoWeek(2026, 53)
    page.refresh()
    assert page._tree.currentItem() is not None
    assert page._tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == IsoWeek(2026, 53)
