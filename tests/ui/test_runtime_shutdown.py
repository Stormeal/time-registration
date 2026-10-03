"""Every runtime worker is drained before restore/exit; failed restore resumes service."""

import importlib
import threading

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from qi_flow.ui.operation_controller import OwnedOperationController


def test_shutdown_drains_controllers_and_browser_thread_before_one_ready_signal(qtbot):
    module = importlib.import_module("qi_flow.ui.runtime_lifecycle")
    group = module.ShutdownGroup(QApplication.instance())
    controller = OwnedOperationController(group)
    group.register_controller(controller)
    started, browser_started, cancelled = threading.Event(), threading.Event(), threading.Event()

    def operation(stop):
        started.set()
        while not stop():
            threading.Event().wait(0.01)

    class Browser(QThread):
        def run(self):
            browser_started.set()
            cancelled.wait(2)

    worker = Browser()
    assert group.track_thread(worker, cancelled.set)
    worker.start()
    assert controller.start("sync", operation)
    qtbot.waitUntil(lambda: started.is_set() and browser_started.is_set())
    events = []
    group.ready.connect(lambda: events.append("ready"))
    with qtbot.waitSignal(group.ready):
        group.begin_shutdown()
    assert not controller.busy
    assert not worker.isRunning()
    assert cancelled.is_set()
    group.begin_shutdown()
    assert events == ["ready"]
    assert not controller.start("sync", operation)


def test_restore_waits_for_worker_then_quits_and_failure_resumes(qtbot):
    module = importlib.import_module("qi_flow.ui.runtime_lifecycle")
    group = module.ShutdownGroup(QApplication.instance())
    controller = OwnedOperationController(group)
    group.register_controller(controller)
    events = []
    started = threading.Event()

    def operation(stop):
        started.set()
        while not stop():
            threading.Event().wait(0.01)
        events.append("stopped")

    def restore(value):
        assert not controller.busy
        events.append("restore")
        if value == "invalid":
            raise ValueError("synthetic invalid backup")

    lifecycle = module.RuntimeLifecycle(group, restore)
    lifecycle.ready_to_quit.connect(lambda _: events.append("quit"))
    lifecycle.failed.connect(lambda _: events.append("failed"))
    assert controller.start("sync", operation)
    qtbot.waitUntil(started.is_set)
    lifecycle.restore("invalid")
    qtbot.waitUntil(lambda: "failed" in events)
    assert events == ["stopped", "restore", "failed"]
    assert controller.start("sync", lambda stop: None)
    qtbot.waitUntil(lambda: not controller.busy)
    lifecycle.restore("valid")
    qtbot.waitUntil(lambda: "quit" in events)
    assert events[-2:] == ["restore", "quit"]


def test_external_event_loop_exit_joins_every_worker_before_release(qtbot):
    module = importlib.import_module("qi_flow.ui.runtime_lifecycle")
    group = module.ShutdownGroup(QApplication.instance())
    controller = OwnedOperationController(group)
    group.register_controller(controller)
    started, stopped = threading.Event(), threading.Event()

    def operation(stop):
        started.set()
        while not stop():
            threading.Event().wait(0.01)
        stopped.set()

    assert controller.start("sync", operation)
    qtbot.waitUntil(started.is_set)
    group.wait_for_shutdown()
    assert stopped.is_set()
    assert not controller.busy


def test_duplicate_close_cannot_discard_approved_restart_command(qtbot):
    from qi_flow.application.desktop import RestartCommand

    module = importlib.import_module("qi_flow.ui.runtime_lifecycle")
    group = module.ShutdownGroup(QApplication.instance())
    controller = OwnedOperationController(group)
    group.register_controller(controller)
    started = threading.Event()

    def operation(stop):
        started.set()
        while not stop():
            threading.Event().wait(0.01)

    lifecycle = module.RuntimeLifecycle(group, lambda _: None)
    deliveries = []
    lifecycle.ready_to_quit.connect(deliveries.append)
    assert controller.start("sync", operation)
    qtbot.waitUntil(started.is_set)
    command = RestartCommand("replacement", ())
    lifecycle.exit(command)
    lifecycle.exit()
    qtbot.waitUntil(lambda: bool(deliveries))
    assert deliveries == [command]
