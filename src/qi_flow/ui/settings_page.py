"""Local backup, restore, and CSV export controls."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QDate, QProcess, QThread, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.dsb import DsbService
from qi_flow.application.google_sync import GoogleSyncSettings
from qi_flow.application.google_sync_service import GoogleSyncService
from qi_flow.application.testhuset import TesthusetCredentialStore, TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import IsoWeek
from qi_flow.infrastructure.backups import BackupManager, BackupView
from qi_flow.infrastructure.csv_export import CsvTimesheetExporter
from qi_flow.infrastructure.google_oauth import GoogleOAuthStore
from qi_flow.infrastructure.google_sheets_sync import GoogleSheetsSync
from qi_flow.infrastructure.paths import AppPaths
from qi_flow.infrastructure.startup import StartupManager
from qi_flow.ui.testhuset_credentials_dialog import TesthusetCredentialsDialog
from qi_flow.ui.testhuset_dialog import SheetFactory, TesthusetDialog


class GoogleSyncWorker(QThread):
    completed = Signal(int)
    failed = Signal()

    def __init__(self, service: GoogleSyncService) -> None:
        super().__init__()
        self._service = service

    def run(self) -> None:
        try:
            self.completed.emit(self._service.sync_completed_records())
        except Exception:
            self.failed.emit()


class SettingsPage(QWidget):
    """Keep resilience actions explicit and make their current state visible."""

    preferences_saved = Signal()
    dsb_enabled_changed = Signal(bool)

    def __init__(
        self,
        service: TimeTrackingApplicationService,
        backups: BackupManager,
        exporter: CsvTimesheetExporter,
        paths: AppPaths,
        startup: StartupManager,
        testhuset: TesthusetService | None = None,
        sheet_factory: SheetFactory | None = None,
        credentials: TesthusetCredentialStore | None = None,
        dsb: DsbService | None = None,
        dsb_sheet_factory: SheetFactory | None = None,
        google_sync: GoogleSyncSettings | None = None,
        google_oauth: GoogleOAuthStore | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._backups = backups
        self._exporter = exporter
        self._paths = paths
        self._startup = startup
        self._testhuset = testhuset
        self._sheet_factory = sheet_factory
        self._credentials = credentials
        self._dsb = dsb
        self._dsb_sheet_factory = dsb_sheet_factory
        self._google_sync = google_sync
        self._google_oauth = google_oauth

        title = QLabel("Settings")
        font = title.font()
        font.setPointSize(18)
        font.setBold(True)
        title.setFont(font)

        self._backup_folder = QLineEdit()
        browse = QPushButton("Choose folder")
        browse.clicked.connect(self._choose_backup_folder)
        save_folder = QPushButton("Save folder")
        save_folder.clicked.connect(self._save_backup_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self._backup_folder, 1)
        folder_row.addWidget(browse)
        folder_row.addWidget(save_folder)
        self._backup_warning = QLabel()
        self._backup_warning.setWordWrap(True)
        self._backup_list = QComboBox()
        create_backup = QPushButton("Back up now")
        create_backup.clicked.connect(self._backup_now)
        restore = QPushButton("Restore selected backup")
        restore.clicked.connect(self._restore_selected)
        backup_form = QFormLayout()
        backup_form.addRow("Backup folder", folder_row)
        backup_form.addRow("Status", self._backup_warning)
        backup_form.addRow("Available backups", self._backup_list)
        backup_form.addRow(create_backup, restore)
        backup_group = QGroupBox("Backups and restore")
        backup_group.setLayout(backup_form)

        preferences = service.app_preferences()
        self._rounding = QComboBox()
        for minutes in (1, 5, 10, 15):
            self._rounding.addItem(f"{minutes} minutes", minutes)
        self._rounding.setCurrentIndex((1, 5, 10, 15).index(preferences.rounding_minutes))
        self._target = QSpinBox()
        self._target.setRange(0, 100)
        self._target.setSuffix(" hours")
        self._target.setValue(preferences.weekly_target_minutes // 60)
        self._sleep_enabled = QCheckBox("Detect long sleep gaps")
        self._sleep_enabled.setChecked(preferences.sleep_enabled)
        self._sleep_minutes = QSpinBox()
        self._sleep_minutes.setRange(1, 240)
        self._sleep_minutes.setSuffix(" minutes")
        self._sleep_minutes.setValue(preferences.sleep_threshold_minutes)
        self._theme = QComboBox()
        self._theme.addItem("System", "system")
        self._theme.addItem("Light", "light")
        self._theme.addItem("Dark", "dark")
        self._theme.setCurrentIndex(("system", "light", "dark").index(preferences.theme))
        self._startup_enabled = QCheckBox("Start QI Flow with Windows")
        self._startup_enabled.setChecked(startup.is_enabled())
        save_preferences = QPushButton("Save application settings")
        save_preferences.clicked.connect(self._save_preferences)
        app_form = QFormLayout()
        app_form.addRow("Rounding", self._rounding)
        app_form.addRow("Default weekly target", self._target)
        app_form.addRow(self._sleep_enabled)
        app_form.addRow("Sleep prompt after", self._sleep_minutes)
        app_form.addRow("Theme", self._theme)
        app_form.addRow(self._startup_enabled)
        app_form.addRow(save_preferences)
        application_group = QGroupBox("Application")
        application_group.setLayout(app_form)

        self._scope = QComboBox()
        self._scope.addItems(["Selected week", "Selected month", "All history"])
        self._scope.currentIndexChanged.connect(self._update_scope_hint)
        self._reference_date = QDateEdit(QDate.currentDate())
        self._reference_date.setCalendarPopup(True)
        self._scope_hint = QLabel()
        summary = QPushButton("Export summary CSV")
        summary.clicked.connect(lambda: self._export("summary"))
        detailed = QPushButton("Export detailed CSV")
        detailed.clicked.connect(lambda: self._export("detailed"))
        export_form = QFormLayout()
        export_form.addRow("Period", self._scope)
        export_form.addRow("Reference date", self._reference_date)
        export_form.addRow("Selection", self._scope_hint)
        export_form.addRow(summary, detailed)
        export_group = QGroupBox("CSV export")
        export_group.setLayout(export_form)

        data_path = QLineEdit(str(paths.data_dir))
        data_path.setReadOnly(True)
        open_logs = QPushButton("Open log folder")
        open_logs.clicked.connect(self._open_log_folder)
        diagnostics_form = QFormLayout()
        diagnostics_form.addRow("Application data", data_path)
        diagnostics_form.addRow("Diagnostics", open_logs)
        diagnostics_group = QGroupBox("Local diagnostics")
        diagnostics_group.setLayout(diagnostics_form)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addWidget(title)
        layout.addWidget(application_group)
        if google_sync is not None:
            sync_group = QGroupBox("Google Sheets sync")
            sync_form = QFormLayout(sync_group)
            self._sync_sheet_url = QLineEdit()
            self._sync_client_id = QLineEdit()
            self._sync_client_secret = QLineEdit()
            self._sync_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
            saved_sync = google_sync.load()
            if saved_sync is not None:
                self._sync_sheet_url.setText(saved_sync.sheet_url)
                self._sync_client_id.setText(saved_sync.oauth_client_id)
            sync_save = QPushButton("Save sync connection")
            sync_save.clicked.connect(self._save_google_sync)
            save_client = QPushButton("Save OAuth client")
            save_client.clicked.connect(self._save_google_client)
            authorize = QPushButton("Authorize this machine")
            authorize.clicked.connect(self._authorize_google)
            disconnect = QPushButton("Disconnect Google sync")
            disconnect.clicked.connect(self._disconnect_google)
            sync_now = QPushButton("Save & sync now")
            sync_now.clicked.connect(self._sync_google_now)
            self._sync_now = sync_now
            self._sync_progress = QProgressBar()
            self._sync_progress.setVisible(False)
            sync_form.addRow("Shared Sheet URL", self._sync_sheet_url)
            sync_form.addRow("Desktop OAuth client ID", self._sync_client_id)
            sync_form.addRow("Desktop OAuth client secret", self._sync_client_secret)
            if google_oauth is not None:
                sync_form.addRow(save_client)
                sync_form.addRow(authorize, disconnect)
                sync_form.addRow(sync_now)
                sync_form.addRow(self._sync_progress)
                self._sync_status = QLabel()
                self._refresh_google_status()
                sync_form.addRow(self._sync_status)
            sync_form.addRow(sync_save)
            layout.addWidget(sync_group)
        self._testhuset_default = QComboBox()
        self._testhuset_week = QDateEdit(QDate.currentDate())
        self._testhuset_week.setDisplayFormat("dd/MM/yyyy")
        self._testhuset_week.setCalendarPopup(True)
        if testhuset is not None and sheet_factory is not None:
            group = QGroupBox("Testhuset")
            form = QFormLayout(group)
            scan = QPushButton("Scan Testhuset tasks")
            scan.clicked.connect(self._scan_testhuset)
            save = QPushButton("Save default task")
            save.clicked.connect(self._save_testhuset_default)
            save_sign_in = QPushButton("Save sign-in in Windows")
            save_sign_in.clicked.connect(self._save_testhuset_sign_in)
            forget_sign_in = QPushButton("Forget saved sign-in")
            forget_sign_in.clicked.connect(self._forget_testhuset_sign_in)
            form.addRow("Scan week containing", self._testhuset_week)
            form.addRow(scan)
            form.addRow("Default project / task", self._testhuset_default)
            form.addRow(save)
            if credentials is not None:
                form.addRow(save_sign_in, forget_sign_in)
            layout.addWidget(group)
            self._refresh_testhuset()
        self._dsb_default = QComboBox()
        self._dsb_week = QDateEdit(QDate.currentDate())
        self._dsb_week.setDisplayFormat("dd/MM/yyyy")
        self._dsb_enabled = QCheckBox("Use DSB time registration")
        if dsb is not None and dsb_sheet_factory is not None:
            group = QGroupBox("DSB")
            form = QFormLayout(group)
            self._dsb_enabled.setChecked(dsb.is_enabled())
            self._dsb_enabled.toggled.connect(self._save_dsb_enabled)
            scan = QPushButton("Scan DSB allocations")
            scan.clicked.connect(self._scan_dsb)
            save = QPushButton("Save default allocation")
            save.clicked.connect(self._save_dsb_default)
            form.addRow(self._dsb_enabled)
            form.addRow("Scan week containing", self._dsb_week)
            form.addRow(scan)
            form.addRow("Default allocation", self._dsb_default)
            form.addRow(save)
            layout.addWidget(group)
            self._refresh_dsb()
        layout.addWidget(backup_group)
        layout.addWidget(export_group)
        layout.addWidget(diagnostics_group)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        status = self._backups.status()
        self._backup_folder.setText(str(status.folder))
        self._backup_warning.setText(status.warning or "Daily backup is healthy.")
        self._backup_list.clear()
        for backup in self._backups.list_backups(status.folder):
            self._backup_list.addItem(backup.created_at.strftime("%d/%m/%Y %H:%M"), backup)
        if self._backup_list.count() == 0:
            self._backup_list.addItem("No valid backups available", None)
        self._update_scope_hint()

    def _choose_backup_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose backup folder", self._backup_folder.text()
        )
        if selected:
            self._backup_folder.setText(selected)

    def _refresh_testhuset(self) -> None:
        self._testhuset_default.clear()
        self._testhuset_default.addItem("Choose a task after scanning", None)
        if self._testhuset is None:
            return
        try:
            for task in self._testhuset.tasks():
                self._testhuset_default.addItem(task.label, task.id)
            default = self._testhuset.default_task_id()
            index = self._testhuset_default.findData(default)
            if default is not None and index < 0:
                self._testhuset_default.setItemText(
                    0, "Default task unavailable — select a current task"
                )
            self._testhuset_default.setCurrentIndex(max(0, index))
        except (OSError, ValueError):
            self._testhuset_default.setItemText(0, "Task cache unavailable — scan again")

    def showEvent(self, event: QShowEvent) -> None:
        self._refresh_testhuset()
        self._refresh_dsb()
        super().showEvent(event)

    def _refresh_dsb(self) -> None:
        self._dsb_default.clear()
        self._dsb_default.addItem("Choose an allocation after scanning", None)
        if self._dsb is None:
            return
        try:
            for allocation in self._dsb.tasks():
                self._dsb_default.addItem(allocation.label, allocation.id)
            index = self._dsb_default.findData(self._dsb.default_task_id())
            self._dsb_default.setCurrentIndex(max(0, index))
        except (OSError, ValueError):
            self._dsb_default.setItemText(0, "Allocation cache unavailable — scan again")

    def _save_google_sync(self) -> None:
        if self._google_sync is None:
            return
        try:
            self._google_sync.save_values(self._sync_sheet_url.text(), self._sync_client_id.text())
            if hasattr(self, "_sync_status"):
                self._sync_status.setText("Status: sync connection saved")
        except ValueError as error:
            self._show_error("Could not save sync connection", str(error))

    def _choose_google_client(self) -> None:
        if self._google_oauth is None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose Google OAuth client JSON", "", "JSON files (*.json)"
        )
        if not filename:
            return
        try:
            client_id = self._google_oauth.save_client_json(
                Path(filename).read_text(encoding="utf-8")
            )
            self._sync_client_id.setText(client_id)
        except (OSError, ValueError) as error:
            self._show_error("Could not save Google client", str(error))

    def _save_google_client(self) -> None:
        if self._google_oauth is None:
            return
        try:
            self._google_oauth.save_client(
                self._sync_client_id.text(), self._sync_client_secret.text()
            )
            self._sync_client_secret.clear()
        except ValueError as error:
            self._show_error("Could not save Google client", str(error))

    def _authorize_google(self) -> None:
        if self._google_oauth is None:
            return
        try:
            self._google_oauth.authorize()
            self._refresh_google_status()
        except ValueError as error:
            self._show_error("Google authorization", str(error))

    def _disconnect_google(self) -> None:
        if self._google_oauth is None:
            return
        try:
            self._google_oauth.disconnect()
            self._refresh_google_status()
        except ValueError as error:
            self._show_error("Google authorization", str(error))

    def _sync_google_now(self) -> None:
        if self._google_sync is None or self._google_oauth is None:
            return
        try:
            # Syncing persists the values shown in the form. Users should not have to
            # discover and click a separate save action after OAuth authorization.
            configuration = self._google_sync.save_values(
                self._sync_sheet_url.text(), self._sync_client_id.text()
            )
            service = GoogleSyncService(
                self._service._uow_factory, GoogleSheetsSync(configuration, self._google_oauth)
            )
            self._sync_progress.setRange(0, 0)
            self._sync_progress.setVisible(True)
            self._sync_now.setEnabled(False)
            self._sync_status.setText("Status: synchronizing completed records…")
            self._sync_worker = GoogleSyncWorker(service)
            self._sync_worker.completed.connect(self._sync_completed)
            self._sync_worker.failed.connect(self._sync_failed)
            self._sync_worker.start()
        except ValueError as error:
            self._show_error("Google Sheets sync", str(error))

    def _sync_completed(self, count: int) -> None:
        self._sync_progress.setVisible(False)
        self._sync_now.setEnabled(True)
        self._sync_status.setText(f"Status: synchronized {count} completed records")

    def _sync_failed(self) -> None:
        self._sync_progress.setVisible(False)
        self._sync_now.setEnabled(True)
        self._sync_status.setText("Status: synchronization failed")
        self._show_error("Google Sheets sync", "Sync failed. Check authorization and try again.")

    def _refresh_google_status(self) -> None:
        if hasattr(self, "_sync_status") and self._google_oauth is not None:
            self._sync_status.setText(
                "Status: authorized"
                if self._google_oauth.is_authorized()
                else "Status: not authorized"
            )

    def _save_dsb_enabled(self, enabled: bool) -> None:
        if self._dsb is not None:
            self._dsb.set_enabled(enabled)
            self.dsb_enabled_changed.emit(enabled)

    def _scan_dsb(self) -> None:
        if (
            self._dsb is None
            or self._dsb_sheet_factory is None
            or not self._dsb_enabled.isChecked()
        ):
            return
        selected = self._dsb_week.date()
        week = IsoWeek(*date(selected.year(), selected.month(), selected.day()).isocalendar()[:2])
        TesthusetDialog(self._dsb, self._dsb_sheet_factory, week, scan_only=True).exec()
        self._refresh_dsb()

    def _save_dsb_default(self) -> None:
        if self._dsb is None or self._dsb_default.currentData() is None:
            self._show_error(
                "Choose an allocation", "Scan and select your default DSB allocation first."
            )
            return
        try:
            self._dsb.set_default(str(self._dsb_default.currentData()))
        except (OSError, ValueError) as error:
            self._show_error("Could not save allocation", str(error))

    def _scan_testhuset(self) -> None:
        if self._testhuset is None or self._sheet_factory is None:
            return
        selected = self._testhuset_week.date()
        day = date(selected.year(), selected.month(), selected.day())
        week = IsoWeek(*day.isocalendar()[:2])
        dialog = TesthusetDialog(self._testhuset, self._sheet_factory, week, scan_only=True)
        dialog.exec()
        self._refresh_testhuset()

    def _save_testhuset_default(self) -> None:
        if self._testhuset is None:
            return
        task_id = self._testhuset_default.currentData()
        if task_id is None:
            self._show_error("Choose a task", "Scan and select your default Testhuset task first.")
            return
        try:
            self._testhuset.set_default(str(task_id))
        except (OSError, ValueError) as error:
            self._show_error("Could not save task", str(error))

    def _save_testhuset_sign_in(self) -> None:
        if self._credentials is not None:
            TesthusetCredentialsDialog(self._credentials).exec()

    def _forget_testhuset_sign_in(self) -> None:
        if self._credentials is None:
            return
        try:
            self._credentials.clear()
        except ValueError as error:
            self._show_error("Could not forget Testhuset sign-in", str(error))

    def _save_backup_folder(self) -> None:
        try:
            self._backups.set_folder(Path(self._backup_folder.text()))
        except OSError as error:
            self._show_error("Could not save backup folder", str(error))
        self.refresh()

    def _backup_now(self) -> None:
        backup = self._backups.ensure_daily_backup()
        self.refresh()
        if backup is None and self._backups.status().warning:
            self._show_error("Backup failed", self._backups.status().warning or "Unknown error")

    def _save_preferences(self) -> None:
        from qi_flow.application.dto import AppPreferencesView

        try:
            self._service.save_app_preferences(
                AppPreferencesView(
                    rounding_minutes=int(self._rounding.currentData()),
                    weekly_target_minutes=self._target.value() * 60,
                    sleep_enabled=self._sleep_enabled.isChecked(),
                    sleep_threshold_minutes=self._sleep_minutes.value(),
                    theme=str(self._theme.currentData()),
                )
            )
            self._startup.set_enabled(self._startup_enabled.isChecked())
            self._service.complete_setup()
            self.preferences_saved.emit()
        except (RuntimeError, ValueError) as error:
            self._show_error("Could not save settings", str(error))

    def _open_log_folder(self) -> None:
        QProcess.startDetached("explorer", [str(self._paths.log_dir)])

    def _restore_selected(self) -> None:
        backup = self._backup_list.currentData()
        if not isinstance(backup, BackupView):
            return
        if self._service.active_state().session_id is not None:
            self._show_error(
                "Finish work first", "Finish or resolve the active work session before restoring."
            )
            return
        answer = QMessageBox.question(
            self,
            "Restore backup",
            "QI Flow will create a safety backup, restore the selected backup, and restart. "
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            self._backups.restore(backup)
        except (OSError, ValueError) as error:
            self._show_error("Restore failed", str(error))
            return
        QProcess.startDetached(sys.executable, sys.argv[1:])
        QCoreApplication.quit()

    def _export(self, kind: str) -> None:
        start, end = self._selected_range()
        suggested = (
            f"qi-flow-{kind}-{start.isoformat()}-{(end - timedelta(days=1)).isoformat()}.csv"
        )
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", suggested, "CSV files (*.csv)"
        )
        if not filename:
            return
        path = Path(filename)
        try:
            if kind == "summary":
                self._exporter.write_summary(path, self._service.summaries_for_range(start, end))
            else:
                self._exporter.write_detailed(path, start, end)
        except OSError as error:
            self._show_error("Export failed", str(error))

    def _selected_range(self) -> tuple[date, date]:
        if self._scope.currentText() == "All history":
            return self._service.history_range()
        selected = self._reference_date.date()
        reference = date(selected.year(), selected.month(), selected.day())
        if self._scope.currentText() == "Selected month":
            start = reference.replace(day=1)
            return start, (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return reference - timedelta(days=reference.isoweekday() - 1), reference + timedelta(
            days=8 - reference.isoweekday()
        )

    def _update_scope_hint(self) -> None:
        start, end = self._selected_range()
        self._scope_hint.setText(
            f"{start.strftime('%d/%m/%Y')} - {(end - timedelta(days=1)).strftime('%d/%m/%Y')}"
        )

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)
