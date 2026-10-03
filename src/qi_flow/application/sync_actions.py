"""Application commands/queries injected into Settings by the composition root."""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta

from qi_flow.application.dto import SyncStatusView
from qi_flow.application.google_sync import GoogleSyncConfiguration, GoogleSyncSettings
from qi_flow.application.google_sync_service import (
    GoogleSyncUpgradeRequiredError,
    SyncResult,
    SyncService,
)
from qi_flow.application.ports import Clock, GoogleConnection, UnitOfWork
from qi_flow.application.sync_migration import MigrationStatus, SyncMigration
from qi_flow.application.sync_models import EntityKey, SyncConflictReview, SyncProblem, SyncTarget

type ServiceFactory = Callable[
    [GoogleSyncConfiguration, SyncTarget, int, Callable[[], bool]], SyncService
]
type MigrationFactory = Callable[[GoogleSyncConfiguration, int, Callable[[], bool]], SyncMigration]


class SyncAuthorizationRequiredError(ValueError):
    pass


class GoogleSyncActions:
    def __init__(
        self,
        factory: Callable[[], UnitOfWork],
        settings: GoogleSyncSettings,
        authorization: GoogleConnection,
        clock: Clock,
        service_factory: ServiceFactory,
        migration_factory: MigrationFactory,
    ) -> None:
        self._factory, self._settings, self._authorization, self._clock = (
            factory,
            settings,
            authorization,
            clock,
        )
        self._service_factory, self._migration_factory = service_factory, migration_factory
        self._review_services: dict[tuple[SyncTarget, int], SyncService] = {}

    def _configuration(self) -> GoogleSyncConfiguration:
        configuration = self._settings.load()
        if configuration is None:
            raise GoogleSyncUpgradeRequiredError(
                "Save a sync connection and review migration first."
            )
        return configuration

    def _binding(self) -> tuple[GoogleSyncConfiguration, SyncTarget, int]:
        configuration = self._configuration()
        target = self._settings.active_target()
        if target is None:
            raise GoogleSyncUpgradeRequiredError(
                "Review the shared Sheet migration before syncing. "
                "Local tracking remains available."
            )
        return configuration, target, self._settings.generation()

    def _require_authorization(self) -> None:
        if not self._authorization.is_authorized():
            raise SyncAuthorizationRequiredError(
                "Authorize this computer before contacting the shared Sheet."
            )

    def synchronize(self, cancelled: Callable[[], bool]) -> SyncResult:
        configuration, target, generation = self._binding()
        self._require_authorization()
        service = self._service_factory(configuration, target, generation, cancelled)
        return service.run_once(
            cancelled=cancelled, deadline=self._clock.now() + timedelta(minutes=2)
        )

    def _review_service(self) -> SyncService:
        configuration, target, generation = self._binding()
        key = target, generation
        if key not in self._review_services:
            self._review_services = {
                key: self._service_factory(configuration, target, generation, lambda: False)
            }
        return self._review_services[key]

    def review(self, conflict_id: str) -> SyncConflictReview:
        return self._review_service().review(conflict_id)

    def resolve(
        self,
        conflict_id: str,
        reviewed_head_ids: frozenset[str],
        chosen_payloads: Mapping[EntityKey, Mapping[str, object] | None],
    ) -> None:
        self._review_service().resolve(conflict_id, reviewed_head_ids, chosen_payloads)

    def status(self) -> SyncStatusView:
        if self._settings.load() is None:
            return SyncStatusView("unconfigured")
        target = self._settings.active_target()
        if target is None:
            return SyncStatusView("migration_required")
        with self._factory() as uow:
            repo = uow.sync_for(target)
            conflicts, problems, pending = repo.conflicts(), repo.problems(), repo.pending()
            staged = bool(repo.get_state("staged_reconciliation")) or bool(
                repo.get_state("incomplete_groups")
            )
            last = repo.get_state("last_success")
            last_success = datetime.fromisoformat(last) if isinstance(last, str) else None
        state = (
            "authorization_required"
            if not self._authorization.is_authorized()
            else "invalid_data"
            if problems
            else "conflict"
            if conflicts
            else "pending"
            if pending or staged
            else "synced"
            if last_success
            else "ready"
        )
        return SyncStatusView(
            state, len(pending), len(conflicts), len(problems), last_success, conflicts
        )

    def problems(self) -> tuple[SyncProblem, ...]:
        _, target, _ = self._binding()
        with self._factory() as uow:
            return uow.sync_for(target).problems()

    def migrate(
        self,
        action: str,
        participants: Sequence[str],
        participant: str,
        *,
        writers_paused: bool,
        cancelled: Callable[[], bool],
    ) -> MigrationStatus:
        self._require_authorization()
        migration = self._migration_factory(
            self._configuration(), self._settings.generation(), cancelled
        )
        if action == "begin":
            migration.begin(participants, writers_paused=writers_paused)
        elif action == "contribute":
            if not writers_paused:
                raise ValueError("All V1 writers must be paused and upgraded first.")
            migration.contribute(participant)
        elif action == "complete":
            if not writers_paused:
                raise ValueError("All V1 writers must be paused and upgraded first.")
            migration.complete(participant)
        else:
            raise ValueError("Choose a supported migration action.")
        return migration.status()
