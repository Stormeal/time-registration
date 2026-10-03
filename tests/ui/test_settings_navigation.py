"""Settings navigation keeps the overview concise and all actions reachable."""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QGroupBox, QPushButton, QScrollArea

from qi_flow.ui.settings_page import SettingsPage


def page_for(rig, qtbot) -> SettingsPage:
    page = SettingsPage(*rig.window_args)
    qtbot.addWidget(page)
    page.show()
    return page


def open_section(page: SettingsPage, name: str) -> None:
    button = next(
        button
        for button in page.findChildren(QPushButton)
        if button.accessibleName() == f"Open {name}"
    )
    button.click()


class SyncSettings:
    def load(self):
        return None


class OAuth:
    def __init__(self) -> None:
        self.authorized = False

    def is_authorized(self) -> bool:
        return self.authorized

    def authorize(self, *, cancelled, timeout_seconds) -> None:
        self.authorized = True

    def disconnect(self) -> None:
        self.authorized = False


class Tasks:
    def tasks(self):
        return []

    def default_task_id(self):
        return None


class DsbTasks(Tasks):
    def branch_tasks(self):
        return ()

    def included_branches(self):
        return frozenset()

    def is_enabled(self) -> bool:
        return False


def test_overview_opens_a_section_and_returns_without_losing_settings(qtbot, rig) -> None:
    page = page_for(rig, qtbot)
    assert page._stack.currentIndex() == 0
    assert not page._rounding.isVisible()

    open_section(page, "Tracking and appearance")
    assert page._rounding.isVisible()
    page._target.setValue(32)
    next(
        button for button in page.findChildren(QPushButton) if button.objectName() == "saveSettings"
    ).click()
    assert rig.service.app_preferences().weekly_target_minutes == 32 * 60

    page._back.click()
    assert page._stack.currentIndex() == 0
    assert not page._rounding.isVisible()


def test_each_settings_group_has_at_most_one_button(qtbot, rig) -> None:
    oauth = OAuth()
    page = SettingsPage(
        *rig.window_args,
        google_sync=SyncSettings(),
        google_oauth=oauth,
        testhuset=Tasks(),
        sheet_factory=lambda: None,
        credentials=object(),
        dsb=DsbTasks(),
        dsb_sheet_factory=lambda: None,
    )
    qtbot.addWidget(page)
    page.show()
    groups = [
        group
        for group in page.findChildren(QGroupBox)
        if group.objectName() in {"settingsCategory", "settingsGroup"}
    ]

    assert groups
    assert all(len(group.findChildren(QPushButton)) <= 1 for group in groups)
    assert all(group.accessibleDescription() for group in groups)

    open_section(page, "Google Sheets")
    assert not oauth.authorized
    page._google_auth_button.click()
    qtbot.waitUntil(lambda: oauth.authorized and not page._google_controller.busy)
    assert page._google_auth_button.text() == "Disconnect this computer"
    page._google_auth_button.click()
    assert not oauth.authorized


def test_narrow_settings_scroll_vertically_without_horizontal_overflow(qtbot, rig) -> None:
    page = SettingsPage(*rig.window_args)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    scroll.resize(600, 520)
    qtbot.addWidget(scroll)
    scroll.show()

    open_section(page, "Tracking and appearance")

    assert scroll.verticalScrollBar().maximum() > 0
    assert scroll.horizontalScrollBar().maximum() == 0
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    page._back.click()
    assert scroll.verticalScrollBar().value() == 0


def test_choosing_a_backup_folder_saves_it_immediately(qtbot, rig, monkeypatch, tmp_path) -> None:
    page = page_for(rig, qtbot)
    folder = tmp_path / "chosen-backups"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: str(folder))

    open_section(page, "Backups")
    next(
        button for button in page.findChildren(QPushButton) if button.text() == "Change folder…"
    ).click()

    assert rig.backups.status().folder == Path(folder)
    assert page._backup_folder.text() == str(folder)


def test_export_uses_selected_csv_format(qtbot, rig, monkeypatch) -> None:
    page = page_for(rig, qtbot)
    selected: list[str] = []
    monkeypatch.setattr(page, "_export", selected.append)

    open_section(page, "Export data")
    page._export_kind.setCurrentIndex(page._export_kind.findData("detailed"))
    next(
        button for button in page.findChildren(QPushButton) if button.text() == "Export CSV…"
    ).click()

    assert selected == ["detailed"]
