"""Backups use their own connection off the UI thread and share a shutdown boundary."""

import importlib
import threading
from datetime import timedelta

from PySide6.QtWidgets import QApplication

from qi_flow.application.backup_schedule import BackupSchedule
from qi_flow.application.dto import StartWorkCommand


def test_daily_backup_is_coalesced_off_ui_thread_and_tracking_stays_responsive(qtbot, rig):
    module = importlib.import_module("qi_flow.ui.backup_controller")
    started, release = threading.Event(), threading.Event()
    threads = []
    original = rig.backups.ensure_daily_backup

    def copy(**kwargs):
        threads.append(threading.get_ident())
        started.set()
        assert release.wait(2)
        return original(**kwargs)

    rig.backups.ensure_daily_backup = copy
    controller = module.BackupController(
        rig.backups,
        BackupSchedule(rig.clock, rig.backups.destination_identity),
        QApplication.instance(),
    )
    qtbot.waitUntil(started.is_set)
    try:
        assert not controller.request_backup()
        rig.service.start_work(StartWorkCommand())
        assert rig.service.active_state().session_id is not None
        assert threads != [threading.get_ident()]
    finally:
        release.set()
        qtbot.waitUntil(lambda: not controller.busy)
        controller.begin_shutdown()
    assert rig.backups.list_backups()


def test_open_controller_creates_next_days_backup_and_new_folder(qtbot, rig, tmp_path):
    module = importlib.import_module("qi_flow.ui.backup_controller")
    controller = module.BackupController(
        rig.backups,
        BackupSchedule(rig.clock, rig.backups.destination_identity),
        QApplication.instance(),
    )
    qtbot.waitUntil(lambda: not controller.busy and len(rig.backups.list_backups()) == 1)
    rig.clock.value += timedelta(days=1)
    controller.poll()
    qtbot.waitUntil(lambda: not controller.busy and len(rig.backups.list_backups()) == 2)
    rig.backups.set_folder(tmp_path / "new-backups")
    controller.poll()
    qtbot.waitUntil(lambda: not controller.busy and len(rig.backups.list_backups()) == 1)
    controller.begin_shutdown()


def test_shutdown_cancels_backup_and_waits_before_signalling_ready(qtbot, rig):
    module = importlib.import_module("qi_flow.ui.backup_controller")
    started = threading.Event()
    deliveries = []

    def copy(*, cancelled, **kwargs):
        started.set()
        while not cancelled():
            threading.Event().wait(0.01)
        return None

    rig.backups.ensure_daily_backup = copy
    controller = module.BackupController(
        rig.backups,
        BackupSchedule(rig.clock, rig.backups.destination_identity),
        QApplication.instance(),
    )
    controller.status_changed.connect(lambda *args: deliveries.append(args))
    qtbot.waitUntil(started.is_set)
    with qtbot.waitSignal(controller.ready_for_shutdown):
        controller.begin_shutdown()
    assert not controller.busy
    assert deliveries == []
    assert not controller.request_backup()


def test_status_delivery_returns_to_qt_thread(qtbot, rig):
    from PySide6.QtCore import QThread

    module = importlib.import_module("qi_flow.ui.backup_controller")
    controller = module.BackupController(
        rig.backups,
        BackupSchedule(rig.clock, rig.backups.destination_identity),
        QApplication.instance(),
    )
    threads = []
    controller.status_changed.connect(lambda _: threads.append(QThread.currentThread()))
    qtbot.waitUntil(lambda: not controller.busy and bool(threads))
    assert threads == [QApplication.instance().thread()]
    controller.begin_shutdown()


def test_failed_copy_warning_survives_until_retry_succeeds(qtbot, rig, monkeypatch):
    module = importlib.import_module("qi_flow.ui.backup_controller")
    attempts = []
    original = rig.backups._create_daily_backup

    def copy(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("synthetic inaccessible folder")
        return original(*args, **kwargs)

    monkeypatch.setattr(rig.backups, "_create_daily_backup", copy)
    controller = module.BackupController(
        rig.backups,
        BackupSchedule(rig.clock, rig.backups.destination_identity),
        QApplication.instance(),
    )
    qtbot.waitUntil(lambda: bool(attempts) and not controller.busy)
    assert rig.backups.status().warning
    rig.clock.value += timedelta(minutes=14)
    controller.poll()
    assert len(attempts) == 1
    assert rig.backups.status().warning
    rig.clock.value += timedelta(minutes=1)
    controller.poll()
    qtbot.waitUntil(lambda: len(attempts) == 2 and not controller.busy)
    assert rig.backups.status().warning is None
    controller.begin_shutdown()
