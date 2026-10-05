"""Local backup, restore, and CSV export controls."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from threading import Event

from PySide6.QtCore import QDate, QProcess, QSize, Qt, QThread, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.backups import BackupOperations, BackupView
from qi_flow.application.desktop import (
    AvailableUpdate,
    ReleaseOperations,
    RuntimeDirectories,
    StartupPreferences,
    TimesheetExporter,
    UpdateError,
)
from qi_flow.application.dsb import DsbService
from qi_flow.application.dto import ReminderSettingsView
from qi_flow.application.google_sync import GoogleSyncSettings
from qi_flow.application.google_sync_service import GoogleSyncUpgradeRequiredError, SyncResult
from qi_flow.application.ports import GoogleConnection
from qi_flow.application.sync_actions import GoogleSyncActions
from qi_flow.application.sync_migration import MigrationStatus
from qi_flow.application.sync_models import (
    SyncAuthorizationRequiredError,
    SyncJobCancelledError,
    SyncRetryError,
    canonical_json,
)
from qi_flow.application.testhuset import TesthusetCredentialStore, TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import IsoWeek
from qi_flow.domain.time_rules import COPENHAGEN
from qi_flow.ui.backup_controller import BackupController
from qi_flow.ui.controls import SettingsWheelGuard
from qi_flow.ui.google_sync_controller import GoogleSyncController
from qi_flow.ui.runtime_lifecycle import ShutdownGroup
from qi_flow.ui.sync_conflict_dialog import SyncConflictDialog
from qi_flow.ui.sync_migration_dialog import MigrationRequest, SyncMigrationDialog
from qi_flow.ui.testhuset_credentials_dialog import TesthusetCredentialsDialog
from qi_flow.ui.testhuset_dialog import SheetFactory, TesthusetDialog


class UpdateCheckWorker(QThread):
    checked = Signal(object)
    failed = Signal(str)

    def __init__(self, client: ReleaseOperations, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self.cancelled = Event()

    def run(self) -> None:
        try:
            result = self._client.check(cancelled=self.cancelled.is_set)
            if not self.cancelled.is_set():
                self.checked.emit(result)
        except UpdateError as error:
            if not self.cancelled.is_set():
                self.failed.emit(str(error))


class UpdateDownloadWorker(QThread):
    downloaded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(
        self,
        client: ReleaseOperations,
        update: AvailableUpdate,
        destination: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._update = update
        self._destination = destination
        self.cancelled = Event()

    def run(self) -> None:
        try:
            result = self._client.download(
                self._update,
                self._destination,
                progress=self.progress.emit,
                cancelled=self.cancelled.is_set,
            )
            if not self.cancelled.is_set():
                self.downloaded.emit(result)
        except UpdateError as error:
            if not self.cancelled.is_set():
                self.failed.emit(str(error))


class SettingsStack(QStackedWidget):
    """Size the outer scroll area for the page currently being shown."""

    def sizeHint(self) -> QSize:
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()


class SettingsPage(QWidget):
    """Keep resilience actions explicit and make their current state visible."""

    preferences_saved = Signal()
    dsb_enabled_changed = Signal(bool)
    restore_requested = Signal(object)
    update_install_requested = Signal(object, object)

    def __init__(
        self,
        service: TimeTrackingApplicationService,
        backups: BackupOperations,
        exporter: TimesheetExporter,
        paths: RuntimeDirectories,
        startup: StartupPreferences,
        testhuset: TesthusetService | None = None,
        sheet_factory: SheetFactory | None = None,
        credentials: TesthusetCredentialStore | None = None,
        dsb: DsbService | None = None,
        dsb_sheet_factory: SheetFactory | None = None,
        google_sync: GoogleSyncSettings | None = None,
        google_oauth: GoogleConnection | None = None,
        releases: ReleaseOperations | None = None,
        google_controller: GoogleSyncController | None = None,
        sync_command: Callable[[Callable[[], bool]], SyncResult] | None = None,
        sync_actions: GoogleSyncActions | None = None,
        backup_controller: BackupController | None = None,
        shutdown: ShutdownGroup | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._shutdown = shutdown
        self._backups = backups
        self._backup_controller = backup_controller
        if backup_controller is not None:
            backup_controller.status_changed.connect(lambda _: self.refresh())
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
        self._sync_command = sync_command
        self._sync_actions = sync_actions
        self._migration_dialog: SyncMigrationDialog | None = None
        self._conflict_dialog: SyncConflictDialog | None = None
        self._sync_event_message: str | None = None
        self._google_controller = google_controller or GoogleSyncController(self)
        self._google_controller.completed.connect(self._google_operation_completed)
        self._google_controller.failed.connect(self._google_operation_failed)
        self._google_controller.busy_changed.connect(self._google_busy_changed)
        self._releases = releases
        self._update_worker: QThread | None = None
        self._pending_update: AvailableUpdate | None = None
        self._update_progress: QProgressBar | None = None

        self._title = QLabel("Settings")
        font = self._title.font()
        font.setPointSize(18)
        font.setBold(True)
        self._title.setFont(font)
        self._introduction = QLabel("Choose an area to change or review.")
        self._introduction.setWordWrap(True)
        self._introduction.setProperty("role", "muted")
        self._back = QPushButton("← All settings")
        self._back.clicked.connect(self._show_overview)
        self._back.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._back.setVisible(False)
        self._stack = SettingsStack()
        overview, overview_layout = self._detail_page()
        self._stack.addWidget(overview)
        general_page, general_layout = self._detail_page()
        backup_page, backup_layout = self._detail_page()
        export_page, export_layout = self._detail_page()
        support_page, support_layout = self._detail_page()

        self._backup_folder = QLineEdit()
        self._backup_folder.setReadOnly(True)
        self._backup_folder.setAccessibleName("Backup folder")
        browse = QPushButton("Change folder…")
        browse.clicked.connect(self._choose_backup_folder)
        self._backup_warning = QLabel()
        self._backup_warning.setWordWrap(True)
        self._backup_list = QComboBox()
        create_backup = QPushButton("Back up now")
        create_backup.clicked.connect(self._backup_now)
        restore = QPushButton("Restore selected backup")
        restore.clicked.connect(self._restore_selected)
        folder_form = QFormLayout()
        folder_form.addRow("Folder", self._backup_folder)
        backup_layout.addWidget(
            self._settings_group(
                "Backup location",
                "Choose a local or OneDrive folder. Your choice saves immediately.",
                folder_form,
                browse,
            )
        )
        backup_status_form = QFormLayout()
        backup_status_form.addRow("Status", self._backup_warning)
        backup_layout.addWidget(
            self._settings_group(
                "Create a backup",
                "QI Flow backs up daily. Make another copy before a risky change.",
                backup_status_form,
                create_backup,
            )
        )
        restore_form = QFormLayout()
        restore_form.addRow("Available backups", self._backup_list)
        backup_layout.addWidget(
            self._settings_group(
                "Restore",
                "Creates a safety backup, restores your data, then restarts QI Flow.",
                restore_form,
                restore,
            )
        )

        preferences = service.app_preferences()
        self._rounding = QComboBox()
        for minutes in (1, 5, 10, 15):
            self._rounding.addItem(f"{minutes} minutes", minutes)
        self._rounding.setCurrentIndex((1, 5, 10, 15).index(preferences.rounding_minutes))
        self._rounding.setToolTip(
            "Rounds work starts down and finishes up; lunch boundaries use nearest rounding. "
            "Changes apply to future timer actions only."
        )
        self._target = QSpinBox()
        self._target.setRange(0, 100)
        self._target.setSuffix(" hours")
        self._target.setValue(preferences.weekly_target_minutes // 60)
        self._sleep_enabled = QCheckBox("Detect long sleep gaps")
        self._sleep_enabled.setChecked(preferences.sleep_enabled)
        self._sleep_enabled.setToolTip(
            "Prompts you to resolve a Windows sleep gap. QI Flow never removes time automatically."
        )
        self._sleep_minutes = QSpinBox()
        self._sleep_minutes.setRange(1, 240)
        self._sleep_minutes.setSuffix(" minutes")
        self._sleep_minutes.setValue(preferences.sleep_threshold_minutes)
        self._sleep_minutes.setToolTip("Prompts for a decision after a sleep gap longer than this.")
        reminders = service.reminder_settings()
        self._work_reminder_enabled = QCheckBox("Remind after long work")
        self._work_reminder_enabled.setChecked(reminders.work_enabled)
        self._work_reminder_enabled.setToolTip(
            "Shows a reminder after elapsed work, including lunch."
        )
        self._work_reminder_minutes = QSpinBox()
        self._work_reminder_minutes.setRange(1, 24 * 60)
        self._work_reminder_minutes.setSuffix(" minutes")
        self._work_reminder_minutes.setValue(reminders.work_minutes)
        self._work_reminder_minutes.setToolTip("Sets the elapsed-work reminder threshold.")
        self._lunch_reminder_enabled = QCheckBox("Remind after long lunch")
        self._lunch_reminder_enabled.setChecked(reminders.lunch_enabled)
        self._lunch_reminder_enabled.setToolTip("Shows a reminder when lunch reaches this length.")
        self._lunch_reminder_minutes = QSpinBox()
        self._lunch_reminder_minutes.setRange(1, 240)
        self._lunch_reminder_minutes.setSuffix(" minutes")
        self._lunch_reminder_minutes.setValue(reminders.lunch_minutes)
        self._lunch_reminder_minutes.setToolTip("Sets the active-lunch reminder threshold.")
        self._theme = QComboBox()
        self._theme.addItem("System", "system")
        self._theme.addItem("Light", "light")
        self._theme.addItem("Dark", "dark")
        self._theme.setCurrentIndex(("system", "light", "dark").index(preferences.theme))
        self._startup_enabled = QCheckBox("Start QI Flow with Windows")
        self._startup_enabled.setChecked(startup.is_enabled())
        save_preferences = QPushButton("Save changes")
        save_preferences.setObjectName("saveSettings")
        save_preferences.setProperty("role", "primary")
        save_preferences.clicked.connect(self._save_preferences)
        tracking_form = QFormLayout()
        tracking_form.addRow("Rounding", self._rounding)
        tracking_form.addRow("Default weekly target", self._target)
        general_layout.addWidget(
            self._settings_group(
                "Work time",
                "Choose future rounding and the target for weeks without a Timesheet override.",
                tracking_form,
            )
        )
        prompt_form = QFormLayout()
        prompt_form.addRow(self._sleep_enabled)
        prompt_form.addRow("Sleep prompt after", self._sleep_minutes)
        prompt_form.addRow(self._work_reminder_enabled, self._work_reminder_minutes)
        prompt_form.addRow(self._lunch_reminder_enabled, self._lunch_reminder_minutes)
        general_layout.addWidget(
            self._settings_group(
                "Prompts and reminders",
                "Sleep gaps ask for your decision; time is never removed automatically. "
                "Work reminders include lunch.",
                prompt_form,
            )
        )
        app_form = QFormLayout()
        app_form.addRow("Theme", self._theme)
        app_form.addRow(self._startup_enabled)
        general_layout.addWidget(
            self._settings_group(
                "Appearance and startup",
                "Follow Windows colours or choose a theme. Starting with Windows is optional.",
                app_form,
            )
        )
        if releases is not None:
            self._check_updates = QPushButton("Check for updates")
            self._check_updates.clicked.connect(self._check_for_updates)
            self._update_status = QLabel("Updates are checked only when you ask.")
            self._update_status.setWordWrap(True)
            self._update_progress = QProgressBar()
            self._update_progress.setRange(0, 100)
            self._update_progress.setFormat("%p%")
            self._update_progress.setAccessibleName("Update download progress")
            self._update_progress.setVisible(False)
            update_form = QFormLayout()
            update_form.addRow("Status", self._update_status)
            update_form.addRow(self._update_progress)
            support_layout.addWidget(
                self._settings_group(
                    "Application updates",
                    "Check stable releases on request. Installing an update requires confirmation.",
                    update_form,
                    self._check_updates,
                )
            )
        save_help = QLabel("Save changes to apply tracking, reminder and appearance settings.")
        save_help.setWordWrap(True)
        save_help.setProperty("role", "muted")
        general_layout.addWidget(save_help)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(save_preferences)
        general_layout.addLayout(save_row)

        self._scope = QComboBox()
        self._scope.addItems(["Selected week", "Selected month", "All history"])
        self._scope.currentIndexChanged.connect(self._update_scope_hint)
        self._reference_date = QDateEdit(QDate.currentDate())
        self._reference_date.setCalendarPopup(True)
        self._scope_hint = QLabel()
        self._export_kind = QComboBox()
        self._export_kind.addItem("Summary — daily totals", "summary")
        self._export_kind.addItem("Detailed — sessions and deductions", "detailed")
        export_button = QPushButton("Export CSV…")
        export_button.clicked.connect(self._export_selected)
        export_form = QFormLayout()
        export_form.addRow("Contents", self._export_kind)
        export_form.addRow("Period", self._scope)
        export_form.addRow("Reference date", self._reference_date)
        export_form.addRow("Selection", self._scope_hint)
        export_layout.addWidget(
            self._settings_group(
                "CSV export",
                "Export daily totals or detailed sessions and deductions to a local CSV file.",
                export_form,
                export_button,
            )
        )

        data_path = QLineEdit(str(paths.data_dir))
        data_path.setReadOnly(True)
        open_logs = QPushButton("Open log folder")
        open_logs.clicked.connect(self._open_log_folder)
        diagnostics_form = QFormLayout()
        diagnostics_form.addRow("Application data", data_path)
        support_layout.addWidget(
            self._settings_group(
                "Local diagnostics",
                "Your data and privacy-safe logs stay in your Windows application-data folder.",
                diagnostics_form,
                open_logs,
            )
        )

        connection_page, connection_layout = self._detail_page()
        workplace_page, workplace_layout = self._detail_page()
        if google_sync is not None:
            self._sync_sheet_url = QLineEdit()
            self._sync_client_id = QLineEdit()
            self._sync_client_secret = QLineEdit()
            self._sync_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
            saved_sync = google_sync.load()
            if saved_sync is not None:
                self._sync_sheet_url.setText(saved_sync.sheet_url)
                self._sync_client_id.setText(saved_sync.oauth_client_id)
            sync_save = QPushButton("Save sync connection")
            self._sync_save = sync_save
            sync_save.clicked.connect(self._save_google_sync)
            sync_form = QFormLayout()
            sync_form.addRow("Shared Sheet URL", self._sync_sheet_url)
            sync_form.addRow("Desktop OAuth client ID", self._sync_client_id)
            connection_layout.addWidget(
                self._settings_group(
                    "Shared Sheet",
                    "Enter the private Sheet and desktop OAuth client ID used on this computer.",
                    sync_form,
                    sync_save,
                )
            )
            if google_oauth is not None:
                save_client = QPushButton("Save OAuth client")
                self._save_client_button = save_client
                save_client.clicked.connect(self._save_google_client)
                client_form = QFormLayout()
                client_form.addRow("Desktop OAuth client secret", self._sync_client_secret)
                connection_layout.addWidget(
                    self._settings_group(
                        "OAuth client",
                        "Save the client credentials for authorization on this Windows account.",
                        client_form,
                        save_client,
                    )
                )
                self._google_auth_button = QPushButton()
                self._google_auth_button.clicked.connect(self._toggle_google_authorization)
                self._authorization_status = QLabel()
                auth_form = QFormLayout()
                auth_form.addRow("Status", self._authorization_status)
                connection_layout.addWidget(
                    self._settings_group(
                        "Authorization",
                        "Connect this computer to sync. Disconnecting removes its authorization.",
                        auth_form,
                        self._google_auth_button,
                    )
                )
                self._sync_now = QPushButton("Save and sync now")
                self._sync_now.clicked.connect(self._sync_google_now)
                self._sync_progress = QProgressBar()
                self._sync_progress.setVisible(False)
                self._sync_status = QLabel(
                    "Syncs when QI Flow opens and after Finish work, once migration is verified."
                )
                self._sync_status.setWordWrap(True)
                sync_action_form = QFormLayout()
                sync_action_form.addRow("Status", self._sync_status)
                sync_action_form.addRow(self._sync_progress)
                connection_layout.addWidget(
                    self._settings_group(
                        "Synchronize",
                        "Sync completed records to your Sheet. No workplace hours are submitted.",
                        sync_action_form,
                        self._sync_now,
                    )
                )
                self._google_cancel = QPushButton("Cancel Google operation")
                self._google_cancel.setEnabled(False)
                self._google_cancel.clicked.connect(self._google_controller.cancel)
                connection_layout.addWidget(
                    self._settings_group(
                        "Cancel",
                        "Stops the current operation. Unverified changes remain pending.",
                        QFormLayout(),
                        self._google_cancel,
                    )
                )
                self._refresh_google_status()
                if sync_actions is not None:
                    self._sync_migration_button = QPushButton("Review shared Sheet migration")
                    self._sync_migration_button.clicked.connect(self._open_sync_migration)
                    connection_layout.addWidget(
                        self._settings_group(
                            "Upgrade shared sync",
                            "Includes every participating computer's history "
                            "and preserves safety copies.",
                            QFormLayout(),
                            self._sync_migration_button,
                        )
                    )
                    self._sync_review_button = QPushButton("Review shared-data conflicts")
                    self._sync_review_button.clicked.connect(self._open_sync_review)
                    connection_layout.addWidget(
                        self._settings_group(
                            "Review",
                            "Choose explicitly between differing histories "
                            "or inspect preserved invalid data.",
                            QFormLayout(),
                            self._sync_review_button,
                        )
                    )
                    self._refresh_sync_status()
        self._testhuset_default = QComboBox()
        self._testhuset_week = QDateEdit(QDate.currentDate())
        self._testhuset_week.setDisplayFormat("dd/MM/yyyy")
        self._testhuset_week.setCalendarPopup(True)
        if testhuset is not None and sheet_factory is not None:
            scan = QPushButton("Scan Testhuset tasks")
            scan.clicked.connect(self._scan_testhuset)
            save = QPushButton("Save default task")
            save.clicked.connect(self._save_testhuset_default)
            scan_form = QFormLayout()
            scan_form.addRow("Week containing", self._testhuset_week)
            workplace_layout.addWidget(
                self._settings_group(
                    "Testhuset task list",
                    "Read tasks for this week. Scanning does not enter hours.",
                    scan_form,
                    scan,
                )
            )
            default_form = QFormLayout()
            default_form.addRow("Default project / task", self._testhuset_default)
            workplace_layout.addWidget(
                self._settings_group(
                    "Testhuset default task",
                    "Use this task unless a work session has its own assignment.",
                    default_form,
                    save,
                )
            )
            if credentials is not None:
                manage_sign_in = QPushButton("Manage sign-in")
                sign_in_menu = QMenu(manage_sign_in)
                sign_in_menu.addAction("Save sign-in in Windows…", self._save_testhuset_sign_in)
                sign_in_menu.addAction("Forget saved sign-in", self._forget_testhuset_sign_in)
                manage_sign_in.setMenu(sign_in_menu)
                workplace_layout.addWidget(
                    self._settings_group(
                        "Testhuset sign-in",
                        "Optional sign-in storage uses Windows Credential Manager for this user.",
                        QFormLayout(),
                        manage_sign_in,
                    )
                )
            self._refresh_testhuset()
        self._dsb_default = QComboBox()
        self._dsb_week = QDateEdit(QDate.currentDate())
        self._dsb_week.setDisplayFormat("dd/MM/yyyy")
        self._dsb_enabled = QCheckBox("Use DSB time registration")
        if dsb is not None and dsb_sheet_factory is not None:
            self._dsb_enabled.setChecked(dsb.is_enabled())
            self._dsb_enabled.toggled.connect(self._save_dsb_enabled)
            scan = QPushButton("Scan DSB allocations")
            scan.clicked.connect(self._scan_dsb)
            save = QPushButton("Save default allocation")
            save.clicked.connect(self._save_dsb_default)
            scan_form = QFormLayout()
            scan_form.addRow(self._dsb_enabled)
            scan_form.addRow("Week containing", self._dsb_week)
            workplace_layout.addWidget(
                self._settings_group(
                    "DSB allocation list",
                    "Opt in before scanning. Scanning reads allocations and does not send hours.",
                    scan_form,
                    scan,
                )
            )
            default_form = QFormLayout()
            default_form.addRow("Default allocation", self._dsb_default)
            workplace_layout.addWidget(
                self._settings_group(
                    "DSB default allocation",
                    "Use this allocation for reviewed DSB registration unless you choose another.",
                    default_form,
                    save,
                )
            )
            self._dsb_branches = QListWidget()
            self._dsb_branches.setAccessibleName("Included in DSB hours")
            self._save_dsb_branches_button = QPushButton("Save included branches")
            self._save_dsb_branches_button.clicked.connect(self._save_dsb_branches)
            branches_form = QFormLayout()
            branches_form.addRow("Included in DSB hours", self._dsb_branches)
            workplace_layout.addWidget(
                self._settings_group(
                    "DSB work branches",
                    "Select Testhuset branches from the latest scan. "
                    "No branch is included automatically; "
                    "unresolved assignments are excluded from DSB hours.",
                    branches_form,
                    self._save_dsb_branches_button,
                )
            )
            self._refresh_dsb_branches()
            self._refresh_dsb()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(self._back)
        layout.addWidget(self._title)
        layout.addWidget(self._introduction)
        layout.addWidget(self._stack)
        self._add_section(
            overview_layout,
            general_page,
            "Tracking and appearance",
            "Set time rounding, weekly targets, sleep prompts, reminders, theme and startup.",
        )
        if google_sync is not None:
            self._add_section(
                overview_layout,
                connection_page,
                "Google Sheets",
                "Connect a private Sheet, authorize this computer and sync completed records.",
            )
        if (testhuset is not None and sheet_factory is not None) or (
            dsb is not None and dsb_sheet_factory is not None
        ):
            self._add_section(
                overview_layout,
                workplace_page,
                "Workplace connections",
                "Manage Testhuset tasks and DSB allocations without submitting hours.",
            )
        self._add_section(
            overview_layout,
            backup_page,
            "Backups",
            "Choose backup storage, create a copy or restore earlier data.",
        )
        self._add_section(
            overview_layout,
            export_page,
            "Export data",
            "Save a summary or detailed CSV for a week, month or all history.",
        )
        self._add_section(
            overview_layout,
            support_page,
            "Updates and diagnostics",
            "Check stable releases and locate private application files and logs.",
        )
        for section_layout in (
            overview_layout,
            general_layout,
            connection_layout,
            workplace_layout,
            backup_layout,
            export_layout,
            support_layout,
        ):
            section_layout.addStretch(1)
        for settings_form in self.findChildren(QFormLayout):
            settings_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.refresh()
        self._wheel_guard = SettingsWheelGuard(self)
        for field in [*self.findChildren(QAbstractSpinBox), *self.findChildren(QComboBox)]:
            field.installEventFilter(self._wheel_guard)

    @staticmethod
    def _detail_page() -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        return page, layout

    @staticmethod
    def _settings_group(
        title: str, description: str, form: QFormLayout, action: QPushButton | None = None
    ) -> QGroupBox:
        group = QGroupBox(title)
        group.setObjectName("settingsGroup")
        group.setAccessibleDescription(description)
        body = QVBoxLayout(group)
        body.setSpacing(10)
        help_text = QLabel(description)
        help_text.setWordWrap(True)
        help_text.setProperty("role", "muted")
        body.addWidget(help_text)
        body.addLayout(form)
        if action is not None:
            action_row = QHBoxLayout()
            action_row.addStretch(1)
            action_row.addWidget(action)
            body.addLayout(action_row)
        return group

    def _add_section(
        self, overview: QVBoxLayout, page: QWidget, title: str, description: str
    ) -> None:
        index = self._stack.addWidget(page)
        open_button = QPushButton("Open settings")
        open_button.setAccessibleName(f"Open {title}")

        def show_section() -> None:
            self._show_section(index, title, description)

        open_button.clicked.connect(show_section)
        card = self._settings_group(title, description, QFormLayout(), open_button)
        card.setObjectName("settingsCategory")
        overview.addWidget(card)

    def _show_section(self, index: int, title: str, description: str) -> None:
        self._stack.setCurrentIndex(index)
        self._title.setText(title)
        self._introduction.setText(description)
        self._back.setVisible(True)
        self._stack.updateGeometry()
        self.updateGeometry()
        self._reset_scroll()

    def _reset_scroll(self) -> None:
        ancestor = self.parentWidget()
        while ancestor is not None:
            if isinstance(ancestor, QScrollArea):
                ancestor.verticalScrollBar().setValue(0)
                break
            ancestor = ancestor.parentWidget()

    def _show_overview(self) -> None:
        self._stack.setCurrentIndex(0)
        self._title.setText("Settings")
        self._introduction.setText("Choose an area to change or review.")
        self._back.setVisible(False)
        self._stack.updateGeometry()
        self.updateGeometry()
        self._reset_scroll()

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
            self._save_backup_folder()

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

    def _refresh_dsb_branches(self) -> None:
        if self._dsb is None or not hasattr(self, "_dsb_branches"):
            return
        self._dsb_branches.clear()
        try:
            selected = self._dsb.included_branches()
            tasks = self._dsb.branch_tasks()
            labels = {task.id: task.label for task in tasks}
            for identifier in sorted(labels.keys() | selected):
                row = QListWidgetItem(
                    labels.get(
                        identifier, f"Previously included {identifier} (not in current scan)"
                    )
                )
                row.setData(Qt.ItemDataRole.UserRole, identifier)
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(
                    Qt.CheckState.Checked if identifier in selected else Qt.CheckState.Unchecked
                )
                self._dsb_branches.addItem(row)
            self._save_dsb_branches_button.setEnabled(True)
        except (OSError, ValueError):
            self._dsb_branches.addItem("Testhuset task cache unavailable — scan again")
            self._save_dsb_branches_button.setEnabled(False)

    def _save_dsb_branches(self) -> None:
        if self._dsb is None:
            return
        selected = {
            str(self._dsb_branches.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self._dsb_branches.count())
            if self._dsb_branches.item(i).checkState() == Qt.CheckState.Checked
        }
        try:
            self._dsb.set_included_branches(selected)
        except (OSError, ValueError) as error:
            self._show_error("Could not save DSB branches", str(error))

    def _save_google_sync(self) -> None:
        if self._google_sync is None or self._google_controller.busy:
            return
        try:
            self._google_sync.save_values(self._sync_sheet_url.text(), self._sync_client_id.text())
            if hasattr(self, "_sync_status"):
                self._sync_status.setText("Status: sync connection saved")
        except ValueError as error:
            self._show_error("Could not save sync connection", str(error))

    def _choose_google_client(self) -> None:
        if self._google_oauth is None or self._google_controller.busy:
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
        if self._google_oauth is None or self._google_controller.busy:
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
        if self._google_controller.authorize(self._google_oauth):
            self._authorization_status.setText("Waiting for browser authorization…")

    def _google_busy_changed(self, busy: bool) -> None:
        if hasattr(self, "_google_auth_button"):
            for field in (
                self._sync_sheet_url,
                self._sync_client_id,
                self._sync_client_secret,
                self._sync_save,
                self._save_client_button,
            ):
                field.setEnabled(not busy)
            self._google_auth_button.setEnabled(not busy)
            self._sync_now.setEnabled(not busy)
            self._google_cancel.setEnabled(busy)
            if self._sync_actions is not None:
                self._sync_migration_button.setEnabled(not busy)
                self._sync_review_button.setEnabled(not busy)
                if not busy:
                    self._refresh_sync_status()

    def _google_operation_completed(self, kind: str, result: object) -> None:
        if kind == "authorize":
            self._sync_event_message = None
            self._refresh_google_status()
        elif kind == "sync" and isinstance(result, SyncResult):
            self._sync_completed(result)
        elif kind == "migration" and isinstance(result, MigrationStatus):
            if self._migration_dialog is not None:
                self._migration_dialog.show_status(
                    result.plan, result.acknowledged, completed=result.completed
                )
        elif kind == "resolve":
            if self._conflict_dialog is not None:
                self._conflict_dialog.accept()
            self.preferences_saved.emit()

    def _google_operation_failed(self, kind: str, error: object) -> None:
        if kind == "authorize":
            self._refresh_google_status()
            if isinstance(error, (SyncJobCancelledError, TimeoutError)):
                self._authorization_status.setText(str(error))
            else:
                self._authorization_status.setText(
                    "Authorization failed; check client setup and retry."
                )
        elif kind == "sync":
            self._sync_progress.setVisible(False)
            if isinstance(error, (GoogleSyncUpgradeRequiredError, SyncAuthorizationRequiredError)):
                message = str(error)
            elif isinstance(error, SyncJobCancelledError):
                message = "Sync cancelled. Unverified changes remain pending."
            elif isinstance(error, SyncRetryError):
                message = str(error)
            elif isinstance(error, OSError):
                message = "Offline or request timed out. Pending changes are retained."
            else:
                message = "Sync could not be verified. Check authorization and shared-data review."
            self._sync_status.setText(message)
            self._sync_event_message = message
        elif kind in {"migration", "resolve"}:
            if isinstance(error, (ValueError, DomainError, TimeoutError)):
                message = str(error)
            else:
                message = (
                    "Could not verify this operation. Safety copies and pending "
                    "work are retained; check access and retry."
                )
            if kind == "migration" and self._migration_dialog is not None:
                self._migration_dialog.show_failure(message)
            elif self._conflict_dialog is not None:
                self._conflict_dialog.show_failure(message)

    def _disconnect_google(self) -> None:
        if self._google_oauth is None or self._google_controller.busy:
            return
        try:
            self._google_oauth.disconnect()
            self._refresh_google_status()
        except ValueError as error:
            self._show_error("Google authorization", str(error))

    def _toggle_google_authorization(self) -> None:
        if self._google_oauth is None or self._google_controller.busy:
            return
        if self._google_oauth.is_authorized():
            self._disconnect_google()
        else:
            self._authorize_google()

    def _sync_google_now(self) -> None:
        if self._google_sync is None or self._google_oauth is None:
            return
        try:
            # Syncing persists the values shown in the form. Users should not have to
            # discover and click a separate save action after OAuth authorization.
            self._google_sync.save_values(self._sync_sheet_url.text(), self._sync_client_id.text())
            command = (
                self._sync_actions.synchronize
                if self._sync_actions is not None
                else self._sync_command
            )
            if command is None:
                raise GoogleSyncUpgradeRequiredError(
                    "Review the shared Sheet migration before syncing. "
                    "Local tracking remains available."
                )
            self._sync_progress.setRange(0, 0)
            self._sync_progress.setVisible(True)
            self._sync_now.setEnabled(False)
            self._sync_status.setText("Status: synchronizing completed records…")
            self._sync_event_message = None
            self._google_controller.start("sync", command, valid=self._google_job_validity())
        except ValueError as error:
            self._sync_status.setText(str(error))

    def _sync_completed(self, result: SyncResult) -> None:
        self._sync_event_message = None
        self._sync_progress.setVisible(False)
        self._sync_now.setEnabled(True)
        states = {
            "synced": "Verified sync",
            "pending": "Changes pending",
            "conflict": "Conflicts require review",
            "invalid_data": "Invalid shared data requires review",
        }
        text = (
            f"{states.get(result.state, result.state)}; "
            f"{result.pending_count} pending; {result.conflict_count} conflicts."
        )
        if result.last_success is not None:
            text += " Last verified sync: " + result.last_success.astimezone(COPENHAGEN).strftime(
                "%d/%m/%Y %H:%M %Z"
            )
        self._sync_status.setText(text)
        self.preferences_saved.emit()

    def _refresh_sync_status(self) -> None:
        if self._sync_actions is None or self._google_controller.busy:
            return
        status = self._sync_actions.status()
        descriptions = {
            "unconfigured": "Save a private Sheet connection first.",
            "migration_required": "Review migration with every participating "
            "computer before syncing.",
            "authorization_required": "Authorize this computer. Local changes remain pending.",
            "ready": "Migration is verified. Ready to sync.",
            "synced": "Last sync verified.",
            "pending": "Changes or incomplete history are pending verification.",
            "conflict": "Differing histories require an explicit review.",
            "invalid_data": "Preserved invalid data requires review; publication is paused.",
        }
        text = (
            f"{self._sync_event_message or descriptions.get(status.state, status.state)} "
            f"{status.pending_count} pending; {status.conflict_count} conflicts; "
            f"{status.problem_count} data issues."
        )
        if status.last_success:
            text += " Last verified sync: " + status.last_success.astimezone(COPENHAGEN).strftime(
                "%d/%m/%Y %H:%M %Z"
            )
        self._sync_status.setText(text)
        self._sync_review_button.setEnabled(bool(status.conflict_count or status.problem_count))
        self._sync_now.setEnabled(
            status.state not in {"unconfigured", "migration_required", "authorization_required"}
        )

    def _open_sync_migration(self) -> None:
        if self._sync_actions is None or self._google_sync is None or self._google_controller.busy:
            return
        try:
            self._google_sync.save_values(self._sync_sheet_url.text(), self._sync_client_id.text())
        except ValueError as error:
            self._sync_status.setText(str(error))
            return
        dialog = SyncMigrationDialog(self)
        self._migration_dialog = dialog
        dialog.migration_requested.connect(self._start_sync_migration)
        dialog.cancel_requested.connect(self._google_controller.cancel)
        dialog.finished.connect(self._migration_closed)
        dialog.show()

    def _migration_closed(self) -> None:
        self._migration_dialog = None

    def _start_sync_migration(self, request: MigrationRequest) -> None:
        actions = self._sync_actions
        if actions is None:
            return
        self._google_controller.start(
            "migration",
            lambda cancelled: actions.migrate(
                request.action,
                request.participants,
                request.participant,
                writers_paused=request.writers_paused,
                cancelled=cancelled,
            ),
            valid=self._google_job_validity(),
        )

    def _open_sync_review(self) -> None:
        if self._sync_actions is None or self._google_controller.busy:
            return
        try:
            status = self._sync_actions.status()
            if not status.conflicts:
                self._show_sync_problems()
                return
            review = self._sync_actions.review(status.conflicts[0].conflict_id)
        except (ValueError, DomainError) as error:
            self._sync_status.setText(str(error))
            return
        dialog = SyncConflictDialog(review, self)
        self._conflict_dialog = dialog
        dialog.resolution_requested.connect(self._resolve_sync_conflict)
        dialog.finished.connect(self._conflict_closed)
        dialog.show()

    def _conflict_closed(self) -> None:
        self._conflict_dialog = None

    def _resolve_sync_conflict(self, conflict_id: str, heads: object, payloads: object) -> None:
        actions = self._sync_actions
        if actions is None or not isinstance(heads, frozenset) or not isinstance(payloads, dict):
            return

        def resolve(cancelled: Callable[[], bool]) -> None:
            if cancelled():
                raise SyncJobCancelledError("Resolution cancelled before saving.")
            actions.resolve(conflict_id, heads, payloads)

        self._google_controller.start("resolve", resolve, valid=self._google_job_validity())

    def _google_job_validity(self) -> Callable[[], bool]:
        settings = self._google_sync
        generation = settings.generation() if settings is not None else None
        return lambda: settings is None or settings.generation() == generation

    def _show_sync_problems(self) -> None:
        if self._sync_actions is None:
            return
        problems = self._sync_actions.problems()
        if not problems:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Preserved shared-data issues")
        layout = QVBoxLayout(dialog)
        description = QLabel(
            "Publication is paused. Review the preserved data below. Pause and upgrade "
            "any V1 writers. Unsupported or damaged histories need a fresh migration into "
            "a new private Sheet; retain the old Sheet and safety copies."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        raw = QPlainTextEdit(
            "\n\n".join(
                canonical_json({"reason": p.reason, "source": p.source, "preserved_data": p.raw})
                for p in problems
            )
        )
        raw.setReadOnly(True)
        layout.addWidget(raw)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(680, 520)
        dialog.show()

    def _refresh_google_status(self) -> None:
        if self._google_oauth is None or not hasattr(self, "_authorization_status"):
            return
        authorized = self._google_oauth.is_authorized()
        self._authorization_status.setText("Connected" if authorized else "Not connected")
        self._google_auth_button.setText(
            "Disconnect this computer" if authorized else "Authorize this computer"
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
        TesthusetDialog(
            self._dsb, self._dsb_sheet_factory, week, scan_only=True, shutdown=self._shutdown
        ).exec()
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
        dialog = TesthusetDialog(
            self._testhuset, self._sheet_factory, week, scan_only=True, shutdown=self._shutdown
        )
        dialog.exec()
        self._refresh_testhuset()
        self._refresh_dsb_branches()

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
        if self._backup_controller is not None:
            self._backup_controller.poll()

    def _backup_now(self) -> None:
        if self._backup_controller is not None:
            self._backup_controller.request_backup(force=True)
            return
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
            self._service.set_reminder_settings(
                ReminderSettingsView(
                    work_enabled=self._work_reminder_enabled.isChecked(),
                    work_minutes=self._work_reminder_minutes.value(),
                    lunch_enabled=self._lunch_reminder_enabled.isChecked(),
                    lunch_minutes=self._lunch_reminder_minutes.value(),
                )
            )
            self._service.complete_setup()
            self.preferences_saved.emit()
        except (RuntimeError, ValueError) as error:
            self._show_error("Could not save settings", str(error))

    def _open_log_folder(self) -> None:
        QProcess.startDetached("explorer", [str(self._paths.log_dir)])

    def _check_for_updates(self) -> None:
        if self._releases is None or (
            self._update_worker is not None and self._update_worker.isRunning()
        ):
            return
        if self._update_progress is not None:
            self._update_progress.setValue(0)
            self._update_progress.setVisible(False)
        self._check_updates.setEnabled(False)
        self._update_status.setText("Checking the QI Flow release service…")
        worker = UpdateCheckWorker(self._releases, self)
        worker.checked.connect(self._update_check_finished)
        worker.failed.connect(self._update_failed)
        worker.finished.connect(
            lambda: self._check_updates.setEnabled(self._update_worker is worker)
        )
        self._update_worker = worker
        if self._shutdown is not None and not self._shutdown.track_thread(
            worker, worker.cancelled.set
        ):
            return
        worker.start()

    def _update_check_finished(self, update: object) -> None:
        if update is None:
            if self._update_progress is not None:
                self._update_progress.setVisible(False)
            self._update_status.setText("QI Flow is up to date.")
            return
        releases = self._releases
        if not isinstance(update, AvailableUpdate) or releases is None:
            self._update_failed("The release service returned invalid update information.")
            return
        answer = QMessageBox.question(
            self,
            "QI Flow update available",
            f"Version {update.version} is available. Download and install it now?\n\n"
            "QI Flow will close briefly. Your local data and settings will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            if self._update_progress is not None:
                self._update_progress.setVisible(False)
            self._update_status.setText(f"Version {update.version} is available.")
            return
        self._check_updates.setEnabled(False)
        self._pending_update = update
        if self._update_progress is not None:
            self._update_progress.setValue(0)
            self._update_progress.setVisible(True)
        self._set_update_download_status(0, update.size)
        destination = self._paths.data_dir / "updates" / f"QI-Flow-{update.version}.zip"
        worker = UpdateDownloadWorker(releases, update, destination, self)
        worker.progress.connect(self._update_download_progress)
        worker.downloaded.connect(self._update_downloaded)
        worker.failed.connect(self._update_failed)
        worker.finished.connect(
            lambda: self._check_updates.setEnabled(self._update_worker is worker)
        )
        self._update_worker = worker
        if self._shutdown is not None and not self._shutdown.track_thread(
            worker, worker.cancelled.set
        ):
            return
        worker.start()

    def _update_download_progress(self, received: int, total: int) -> None:
        if total <= 0:
            return
        if self._update_progress is not None:
            self._update_progress.setValue(min(100, received * 100 // total))
        self._set_update_download_status(received, total)

    def _set_update_download_status(self, received: int, total: int) -> None:
        update = self._pending_update
        if update is None:
            return
        mebibyte = 1024 * 1024
        self._update_status.setText(
            f"Downloading version {update.version}… "
            f"{received / mebibyte:.1f} MB of {total / mebibyte:.1f} MB"
        )

    def _update_downloaded(self, archive: object) -> None:
        if not isinstance(archive, Path):
            self._update_failed("The downloaded update could not be staged.")
            return
        if self._pending_update is None:
            self._update_failed("No verified release information is available for this package.")
            return
        if self._update_progress is not None:
            self._update_progress.setValue(100)
            self._update_progress.setVisible(False)
        self._update_status.setText("Download verified. Preparing to install the update…")
        self.update_install_requested.emit(self._pending_update, archive)

    def _update_failed(self, message: str) -> None:
        if self._update_progress is not None:
            self._update_progress.setVisible(False)
        self._check_updates.setEnabled(True)
        self._update_status.setText(message)
        QMessageBox.warning(self, "QI Flow update", message)

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
        self.restore_requested.emit(backup)

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

    def _export_selected(self) -> None:
        kind = self._export_kind.currentData()
        if kind in ("summary", "detailed"):
            self._export(str(kind))

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
