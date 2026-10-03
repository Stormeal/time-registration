"""Temporary Edge adapter for DSB's SSO-protected Min tidsregistrering portal.

No browser state is persisted.  Selectors deliberately use the stable SAP UI5 control ids
visible in the supplied portal markup rather than generated clone ids.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from hashlib import sha256
from tempfile import TemporaryDirectory
from threading import Event
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, expect, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from qi_flow.application.testhuset import HourSlot, WeeklySheet
from qi_flow.domain.models import IsoWeek
from qi_flow.domain.testhuset import ProjectTask, parse_hours

URL = "https://dsbep0u.kmd.dsb.dk:44351/sap/bc/ui2/flp?sap-client=160&sap-language=DA#TimeEntry-manageTimesheet"
MAX_SSO_TAB_HANDOFFS = 3
SSO_WAIT_MILLISECONDS = 120_000
SSO_POLL_MILLISECONDS = 500
SEND_CONFIRMATION_MILLISECONDS = 120_000
NOTIFICATION_SELECTOR = ".sapMMessageToast, .sapMMsgStrip, [role='alert'], [role='status']"
HOUR_COMMIT_MILLISECONDS = 2_000
HOUR_COMMIT_POLL_MILLISECONDS = 100
_DANISH_MONTHS = (
    "januar",
    "februar",
    "marts",
    "april",
    "maj",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "december",
)
_DANISH_SHORT_MONTHS = (
    "jan",
    "feb",
    "mar",
    "apr",
    "maj",
    "jun",
    "jul",
    "aug",
    "sep",
    "okt",
    "nov",
    "dec",
)


def safe_page_location(url: str) -> str:
    """Describe a page without exposing SSO query parameters or tokens."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme else "unknown page"


def is_dsb_portal(url: str) -> bool:
    """Return whether SSO has navigated back to the DSB portal host."""
    return urlparse(url).hostname == "dsbep0u.kmd.dsb.dk"


def is_target_closed(error: PlaywrightError) -> bool:
    """TargetClosedError is not publicly exported by this Playwright version."""
    return type(error).__name__ == "TargetClosedError"


def should_retry_sso_handoff(error: PlaywrightError, handoffs: int) -> bool:
    """Allow the known SAML tab handoff, but never retry arbitrary browser failures."""
    return is_target_closed(error) and handoffs < MAX_SSO_TAB_HANDOFFS


def wait_for_bemandinger(page: Page, cancelled: Event, *, polls: int | None = None) -> bool:
    """Wait for the signed-in portal control while the user completes SSO in Edge."""
    remaining = polls or SSO_WAIT_MILLISECONDS // SSO_POLL_MILLISECONDS
    tab = page.get_by_text(re.compile(r"^\s*Bemandinger\s*$")).first
    for _ in range(remaining):
        if cancelled.is_set():
            raise ValueError("DSB operation cancelled. Rescan before retrying.")
        if tab.is_visible():
            return True
        page.wait_for_timeout(SSO_POLL_MILLISECONDS)
    return False


def allocation_name_column(headers: list[str]) -> int:
    """Locate the allocation-name column without relying on SAP's utility columns."""
    for index, header in enumerate(headers):
        if header.strip() == "Navn":
            return index
    raise ValueError("DSB allocation layout changed; scan cancelled.")


def allocation_id(name: str) -> str:
    """Map a DSB display allocation to the shared numeric task-ID format deterministically."""
    digest = sha256(name.encode("utf-8")).hexdigest()
    return f"{int(digest[:16], 16)}-{int(digest[16:32], 16)}"


def is_send_confirmation(message: str) -> bool:
    """Recognize DSB's Danish or English positive send notifications."""
    if re.search(r"\b(ikke|not|failed|failure|fejl|error|afvist|rejected)\b", message, re.I):
        return False
    return (
        re.search(r"\b(sendt|sent|gemt|saved|registreret|registered|success)\b", message, re.I)
        is not None
    )


def hour_value_matches(actual: str, expected: str) -> bool:
    """Compare a DSB-localized input value to QI Flow's canonical decimal hours."""
    try:
        return parse_hours(actual) == parse_hours(expected)
    except ValueError:
        return False


def date_heading_matches(heading: str, work_date: date) -> bool:
    """Match a complete DSB calendar date, including its month across ISO-year boundaries."""
    day, month, year = work_date.day, work_date.month, work_date.year
    text = heading.casefold()
    month_name = re.escape(_DANISH_MONTHS[month - 1])
    month_short = re.escape(_DANISH_SHORT_MONTHS[month - 1])
    written = rf"(?<!\d)0?{day}(?:\s*\.\s*|\s+)(?:{month_name}|{month_short}\.?)\s+{year}\b"
    numeric = rf"(?<!\d)0?{day}[./-]0?{month}[./-]{year}\b"
    iso = rf"(?<!\d){year}-0?{month}-0?{day}\b"
    return any(re.search(pattern, text) for pattern in (written, numeric, iso))


class DsbBrowser:
    def __init__(self, page: Page, cancelled: Event) -> None:
        self.page, self.cancelled = page, cancelled
        self._entry_week: IsoWeek | None = None

    def _check(self) -> None:
        if self.cancelled.is_set():
            raise ValueError("DSB operation cancelled. Rescan before retrying.")

    def _navigation_tab(self, label: str) -> Locator:
        """Find the visible UI5 navigation control, excluding accessibility-only labels."""
        candidates = [
            *self.page.get_by_role("tab", name=label, exact=True).all(),
            *self.page.get_by_text(re.compile(rf"^\s*{re.escape(label)}\s*$")).all(),
        ]
        for candidate in candidates:
            if candidate.is_visible():
                return candidate
        raise ValueError(f"DSB did not expose a visible {label} navigation control.")

    def _open_entry_week(self, week: IsoWeek) -> None:
        """Select the reviewed ISO week and enter DSB's editable data-record view."""
        self._check()
        self._navigation_tab("Oversigt").click()
        monday = date.fromisocalendar(week.year, week.week, 1)
        calendar_day = self.page.locator(f"[data-sap-day='{monday:%Y%m%d}']")
        sunday = self.page.locator(f"[data-sap-day='{monday + timedelta(days=6):%Y%m%d}']").first
        if not calendar_day.count() or not sunday.count():
            raise ValueError(
                "DSB did not show the requested ISO week in Overview. Select the week in DSB, "
                "then prepare a new review."
            )
        calendar_day.first.click()
        expect(calendar_day.first).to_have_attribute("aria-selected", "true")
        expect(sunday).to_have_attribute("aria-selected", "true")
        self.page.get_by_text("Indtast datarecords", exact=True).click()
        self.page.locator("tr.sapMListTblRow").first.wait_for()
        self._entry_week = week

    def scan(self, week: IsoWeek) -> tuple[ProjectTask, ...]:
        self._check()
        self._entry_week = None
        self._navigation_tab("Bemandinger").click()
        self.page.locator(
            "#application-TimeEntry-manageTimesheet-component---worklist--tasks"
        ).wait_for()
        table_id = "#application-TimeEntry-manageTimesheet-component---worklist--tasks"
        table = self.page.locator(table_id)
        name_column = allocation_name_column(table.locator("thead th").all_inner_texts())
        rows = table.locator("tbody tr.sapMListTblRow")
        tasks: list[ProjectTask] = []
        for row in rows.all():
            cells = row.locator("td").all_inner_texts()
            if len(cells) > name_column and cells[name_column].strip():
                name = cells[name_column].strip().replace("\n", " ")
                tasks.append(ProjectTask(allocation_id(name), "DSB", name))
        if not tasks:
            raise ValueError(
                "No active DSB allocations were found; the previous cache was preserved."
            )
        return tuple(tasks)

    def read(self, slot: HourSlot) -> str:
        week = IsoWeek(*slot.work_date.isocalendar()[:2])
        if self._entry_week != week:
            self._open_entry_week(week)
        row = self._entry_row(slot)
        value = self._hour_field(row).input_value()
        return value.replace(",", ".")

    def write_verified(self, slot: HourSlot) -> None:
        if self._entry_week != IsoWeek(*slot.work_date.isocalendar()[:2]):
            raise ValueError("DSB selected week changed. Prepare a new review before writing.")
        row = self._entry_row(slot)
        field = self._hour_field(row)
        expect(field).to_be_editable()
        field.fill(slot.hours.replace(".", ","))
        field.press("Tab")
        for _ in range(HOUR_COMMIT_MILLISECONDS // HOUR_COMMIT_POLL_MILLISECONDS):
            field = self._hour_field(self._entry_row(slot))
            if hour_value_matches(field.input_value(), slot.hours):
                return
            self.page.wait_for_timeout(HOUR_COMMIT_POLL_MILLISECONDS)
        raise ValueError("DSB did not accept the requested hours. Rescan before retrying.")

    def _hour_field(self, row: Locator) -> Locator:
        fields = row.get_by_role("spinbutton")
        if fields.count() != 1:
            raise ValueError("DSB hours field is ambiguous. Prepare a new review.")
        return fields.first

    def commit_verified(self) -> None:
        """Send the reviewed batch; never approve/lock the DSB week."""
        self._check()
        send = self.page.locator(
            "#application-TimeEntry-manageTimesheet-component---worklist--OverviewSubmitButton"
        )
        send.wait_for()
        if send.is_disabled():
            raise ValueError(
                "DSB has not enabled Send after the requested changes. Rescan before retrying."
            )
        prior_messages = self._visible_notification_messages()
        send.click()
        confirmation = self._wait_for_send_confirmation(prior_messages)
        if confirmation is None:
            raise ValueError(
                "DSB did not show a send confirmation within two minutes. Its browser remains "
                "open until this operation ends; prepare a new review before retrying."
            )

    def _visible_notification_messages(self) -> set[str]:
        messages: set[str] = set()
        for notification in self.page.locator(NOTIFICATION_SELECTOR).all():
            if notification.is_visible():
                text = notification.inner_text().strip()
                if text:
                    messages.add(text)
        return messages

    def _wait_for_send_confirmation(self, prior_messages: set[str]) -> str | None:
        polls = SEND_CONFIRMATION_MILLISECONDS // SSO_POLL_MILLISECONDS
        for _ in range(polls):
            self._check()
            for message in self._visible_notification_messages() - prior_messages:
                if is_send_confirmation(message):
                    return message
            self.page.wait_for_timeout(SSO_POLL_MILLISECONDS)
        return None

    def _entry_row(self, slot: HourSlot) -> Locator:
        if allocation_id(slot.task.task_name) != slot.task.id:
            raise ValueError(
                "DSB allocation identity changed. Rescan allocations and review again."
            )
        groups = []
        for group in self.page.locator("tr.sapMGHLI").all():
            if not group.is_visible():
                continue
            titles = group.locator(".sapMGHLITitle")
            if titles.count() == 1 and date_heading_matches(
                titles.first.inner_text(), slot.work_date
            ):
                groups.append(group)
        if not groups:
            raise ValueError(
                "DSB date row is missing. Select the week in DSB and prepare a new review."
            )
        if len(groups) != 1:
            raise ValueError("DSB date row is duplicated. Prepare a new review before writing.")
        matches: list[Locator] = []
        for row in groups[0].locator("xpath=following-sibling::tr").all():
            if row.locator(".sapMGHLITitle").count():
                break
            if (
                not row.is_visible()
                or "sapMListTblRow" not in (row.get_attribute("class") or "").split()
            ):
                continue
            combos = row.locator("input[role='combobox']")
            if combos.count() != 1:
                raise ValueError("DSB allocation row is ambiguous. Prepare a new review.")
            if combos.first.input_value().strip() == slot.task.task_name:
                matches.append(row)
        if not matches:
            raise ValueError(
                "DSB allocation row is missing for this date. Add it in DSB or select another "
                "allocation, then prepare a new review. Existing rows were left unchanged."
            )
        if len(matches) != 1:
            raise ValueError(
                "DSB allocation row is duplicated. Prepare a new review before writing."
            )
        return matches[0]


@contextmanager
def temporary_sheet(cancelled: Event, status: Callable[[str], None]) -> Iterator[WeeklySheet]:
    with sync_playwright() as playwright, TemporaryDirectory(prefix="qi-flow-dsb-") as profile:
        # DSB's SAML callback closes an off-the-record tab after login. A disposable profile
        # retains the normal Edge window behavior without retaining state after this operation.
        context = playwright.chromium.launch_persistent_context(
            profile,
            channel="msedge",
            headless=False,
            timeout=60_000,
        )
        try:
            page = context.pages[0]
            page.set_default_timeout(120_000)
            page.set_default_navigation_timeout(120_000)
            status("Complete DSB SSO in the temporary Edge window if prompted…")
            diagnostic_page = page
            try:
                for handoffs in range(MAX_SSO_TAB_HANDOFFS + 1):
                    diagnostic_page = page
                    try:
                        page.goto(URL, wait_until="domcontentloaded")
                        status("Waiting for the DSB Bemandinger tab to become available…")
                        if not wait_for_bemandinger(page, cancelled):
                            raise ValueError(
                                "DSB login did not complete within two minutes. Complete SSO "
                                "in Edge, then rescan allocations."
                            )
                        status("Authentication returned to DSB. Opening Min tidsregistrering…")
                        navigation_page = page
                        break
                    except PlaywrightError as error:
                        if not should_retry_sso_handoff(error, handoffs):
                            raise
                        # DSB's SAML sign-in can close its originating tab after a successful
                        # handoff. Its cookies remain in this non-persistent context.
                        status("The DSB sign-in tab closed. Opening Min tidsregistrering…")
                        page = context.new_page()
                        page.set_default_timeout(120_000)
                        page.set_default_navigation_timeout(120_000)
                else:
                    raise RuntimeError("DSB SSO retry loop ended without a page.")
            except (PlaywrightTimeoutError, PlaywrightError) as error:
                try:
                    title = diagnostic_page.title().strip() or "untitled page"
                except (PlaywrightTimeoutError, PlaywrightError):
                    title = "unavailable page title"
                raise ValueError(
                    "The signed-in DSB page did not expose the 'Bemandinger' tab within two "
                    "minutes. Complete DSB SSO in Edge, open Min tidsregistrering if needed, "
                    "then rescan allocations. Final page: "
                    f"{safe_page_location(diagnostic_page.url)} "
                    f"({title})."
                ) from error
            yield DsbBrowser(navigation_page, cancelled)
        finally:
            context.close()
