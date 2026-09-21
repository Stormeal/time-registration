"""Temporary Edge adapter for DSB's SSO-protected Min tidsregistrering portal.

No browser state is persisted.  Selectors deliberately use the stable SAP UI5 control ids
visible in the supplied portal markup rather than generated clone ids.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from threading import Event
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from qi_flow.application.testhuset import HourSlot, WeeklySheet
from qi_flow.domain.models import IsoWeek
from qi_flow.domain.testhuset import ProjectTask, parse_hours

URL = "https://dsbep0u.kmd.dsb.dk:44351/sap/bc/ui2/flp?sap-client=160&sap-language=DA#TimeEntry-manageTimesheet"


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


class DsbBrowser:
    def __init__(self, page: Page, cancelled: Event) -> None:
        self.page, self.cancelled = page, cancelled
        self._entry_week: IsoWeek | None = None

    def _check(self) -> None:
        if self.cancelled.is_set():
            raise ValueError("DSB operation cancelled. Rescan before retrying.")

    def _navigation_tab(self, label: str) -> Locator:
        """Find a UI5 navigation label despite its nested markup or whitespace."""
        return self.page.get_by_text(re.compile(rf"^\s*{re.escape(label)}\s*$")).first

    def _open_entry_week(self, week: IsoWeek) -> None:
        """Select the reviewed ISO week and enter DSB's editable data-record view."""
        self._check()
        self._navigation_tab("Oversigt").click()
        monday = date.fromisocalendar(week.year, week.week, 1)
        calendar_day = self.page.locator(f"[data-sap-day='{monday:%Y%m%d}']")
        if calendar_day.count():
            calendar_day.first.click()
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
        rows = self.page.locator(f"{table_id} tbody tr.sapMListTblRow")
        tasks: list[ProjectTask] = []
        for row in rows.all():
            cells = row.locator("td").all_inner_texts()
            if cells and cells[1].strip():
                name = cells[1].strip().replace("\n", " ")
                tasks.append(ProjectTask(name, "DSB", name))
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
        value = row.locator("input").last.input_value()
        return value.replace(",", ".")

    def write_verified(self, slot: HourSlot) -> None:
        row = self._entry_row(slot)
        combo = row.locator(".sapMComboBoxBase").first
        combo.click()
        self.page.get_by_text(slot.task.task_name, exact=True).last.click()
        field = row.locator("input").last
        field.fill(slot.hours.replace(".", ","))
        field.press("Tab")
        if parse_hours(field.input_value()) != parse_hours(slot.hours):
            raise ValueError("DSB did not accept the requested hours. Rescan before retrying.")

    def commit_verified(self) -> None:
        """Send the reviewed batch; never approve/lock the DSB week."""
        self._check()
        self.page.get_by_role("button", name="Send", exact=True).click()
        self.page.locator(".sapMMessageToast, .sapMMsgStrip").first.wait_for()

    def _entry_row(self, slot: HourSlot) -> Locator:
        marker = self.page.locator(".sapMGHLITitle").filter(
            has_text=re.compile(rf"\b{slot.work_date.day}\b.*{slot.work_date.year}")
        )
        row = marker.first.locator("xpath=following::tr[contains(@class, 'sapMListTblRow')][1]")
        row.locator("input").last.wait_for()
        return row


@contextmanager
def temporary_sheet(cancelled: Event, status: Callable[[str], None]) -> Iterator[WeeklySheet]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=False, timeout=60_000)
        try:
            context = browser.new_context()
            try:
                page = context.new_page()
                page.set_default_timeout(120_000)
                page.set_default_navigation_timeout(120_000)
                status("Complete DSB SSO in the temporary Edge window if prompted…")
                diagnostic_page = page
                try:
                    page.goto(URL, wait_until="domcontentloaded")
                    if not is_dsb_portal(page.url):
                        page.wait_for_url(
                            re.compile(r"^https://dsbep0u\.kmd\.dsb\.dk(?::\d+)?/"),
                            wait_until="domcontentloaded",
                        )
                    status("Authentication returned to DSB. Opening Min tidsregistrering…")
                except PlaywrightError as error:
                    if not is_target_closed(error):
                        raise
                    # DSB's SAML sign-in can close its originating tab after a successful
                    # handoff. The browser context remains available and holds its SSO cookies.
                    status("The DSB sign-in tab closed. Opening Min tidsregistrering…")
                try:
                    navigation_page = context.new_page()
                    diagnostic_page = navigation_page
                    navigation_page.set_default_timeout(120_000)
                    navigation_page.set_default_navigation_timeout(120_000)
                    navigation_page.goto(URL, wait_until="domcontentloaded")
                    status("Waiting for the DSB Bemandinger tab to become available…")
                    navigation_page.get_by_text(re.compile(r"^\s*Bemandinger\s*$")).first.wait_for()
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
        finally:
            browser.close()
