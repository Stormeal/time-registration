"""Testhuset weeksheet2 adapter, inspected on 17 September 2026.

Only browser UI actions write hours. A server success response AND accepted value are
required. No credentials, cookies, trace, HAR, screenshots or page contents are saved.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from threading import Event
from urllib.parse import urlsplit

from playwright.sync_api import (
    Error,
    Page,
    expect,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from qi_flow.application.testhuset import (
    HourSlot,
    TesthusetCredential,
    TesthusetCredentialStore,
    WeeklySheet,
)
from qi_flow.domain.models import IsoWeek
from qi_flow.domain.testhuset import ProjectTask, parse_hours

ORIGIN = "https://testhuset.eazyproject.net"
_LOG = logging.getLogger(__name__)


def _browser_failure_message(stage: str, error: BaseException) -> str:
    """Explain the failing phase without exposing browser/page contents or credentials."""
    if isinstance(error, PlaywrightTimeoutError):
        return (
            f"Testhuset took too long while {stage}. The temporary browser has closed. "
            "Try scanning again after the site is responsive."
        )
    if isinstance(error, PermissionError):
        return (
            "QI Flow could not start its temporary Testhuset browser. Check that Windows security "
            "software or workplace policy allows Microsoft Edge to be automated, then try again."
        )
    if isinstance(error, AssertionError):
        return (
            f"Testhuset did not show the expected weekly sheet while {stage}. Its layout may have "
            "changed or the site may still be loading. No hours were changed; rescan after the "
            "page is ready."
        )
    return (
        f"Testhuset browser failed while {stage}. The temporary browser has closed. "
        "Check that Microsoft Edge is installed, then scan again."
    )


class BrowserCancelled(ValueError):
    pass


class TesthusetBrowser:
    __test__ = False

    def __init__(self, page: Page, cancelled: Event) -> None:
        self.page = page
        self.cancelled = cancelled
        self._week: IsoWeek | None = None

    def _check(self) -> None:
        if self.cancelled.is_set():
            raise BrowserCancelled("Testhuset operation cancelled. Rescan before retrying.")

    def _assert_origin(self) -> None:
        self._check()
        url = urlsplit(self.page.url)
        if url.scheme != "https" or url.netloc != "testhuset.eazyproject.net":
            raise ValueError("Testhuset login expired. Open a new temporary session.")

    def open(self, credential: TesthusetCredential | None = None) -> None:
        self.page.goto(f"{ORIGIN}/weeksheet2.aspx")
        if credential is not None:
            self.sign_in(credential)
        week_header = self.page.locator(".ws-header-week")
        for _ in range(600):
            self._check()
            if week_header.is_visible():
                self._assert_origin()
                return
            self.page.wait_for_timeout(500)
        if self.page.locator("input[type='password']").count():
            raise ValueError("Login timed out. Try scanning again when you are ready.")
        raise ValueError(
            "Testhuset did not open the weekly sheet after login. No hours were changed; "
            "rescan after the site is ready."
        )

    def sign_in(self, credential: TesthusetCredential) -> None:
        password = self.page.locator("input[type='password']")
        if not password.count() or not password.first.is_visible():
            return
        username = self.page.locator(
            "input[type='email'], input[autocomplete='username'], input[name*='user' i], "
            "input[id*='user' i], input[name*='email' i], input[id*='email' i]"
        )
        if not username.count():
            raise ValueError("Testhuset login layout changed. Sign in directly instead.")
        username.first.fill(credential.username)
        password.first.fill(credential.password)
        password.first.press("Enter")

    def current_week(self) -> IsoWeek:
        self._assert_origin()
        text = self.page.locator(".ws-header-week").inner_text()
        match = re.fullmatch(r"Uge\s+(\d{1,2})\s+(\d{4})", text.strip())
        if match is None:
            raise ValueError("Cannot identify the Testhuset week; no hours were changed.")
        return IsoWeek(int(match[2]), int(match[1]))

    def _navigate(self, week: IsoWeek) -> None:
        target = date.fromisocalendar(week.year, week.week, 1)
        current = self.current_week()
        distance = (target - date.fromisocalendar(current.year, current.week, 1)).days // 7
        if abs(distance) > 520:
            raise ValueError("Choose a week within ten years of the current Testhuset week.")
        for _ in range(abs(distance)):
            self._check()
            previous = self.page.locator(".ws-header-week").inner_text()
            title = "< Forrige uge" if distance < 0 else "Næste uge >"
            self.page.locator(f'li[title="{title}"]').click()
            expect(self.page.locator(".ws-header-week")).not_to_have_text(previous)
        if self.current_week() != week:
            raise ValueError("Testhuset did not navigate to the selected ISO week.")
        self._week = week

    def scan(self, week: IsoWeek) -> tuple[ProjectTask, ...]:
        self._assert_origin()
        # Re-fetch server values, including when confirmation follows a long preview pause.
        self.page.reload(wait_until="domcontentloaded")
        try:
            self.page.locator(".ws-header-week").wait_for(state="visible", timeout=20_000)
        except PlaywrightTimeoutError as error:
            raise ValueError(
                "Testhuset did not reload the weekly sheet. No hours were changed; rescan after "
                "the site is ready."
            ) from error
        self._navigate(week)
        # A filtered subset must never silently remove tasks from the complete cache.
        for suffix in ("ShowFavorites", "OnlyTasksWithExistingRegs", "Simplelist"):
            control = self.page.locator(f"#ctl00_ContentPlaceHolder1_CheckBox{suffix}")
            if control.count() and control.is_checked():
                raise ValueError(
                    "Turn off favorites-only, registered-only and combined-row views in "
                    "Testhuset, then scan again."
                )
        if self.page.locator("#tasksearch").input_value().strip():
            raise ValueError("Clear the Testhuset task search before scanning.")
        collapsed = self.page.locator(".ws-toggle-project:has(.fa-angle-right)")
        for _ in range(500):
            self._check()
            if not collapsed.count():
                break
            count = collapsed.count()
            collapsed.first.click()
            expect(collapsed).to_have_count(count - 1)
        else:
            raise ValueError("Could not expand all Testhuset projects.")
        tasks: list[ProjectTask] = []
        for row in self.page.locator("#idTabelUgeseddel .ws-row-task").all():
            identifier = row.get_attribute("id") or ""
            if not re.fullmatch(r"\d+-\d+", identifier):
                raise ValueError("Testhuset task layout changed; scan cancelled.")
            project_id = identifier.split("-")[1]
            project = self.page.locator(f"#IMG-P{project_id} .ws-header-project-name")
            tasks.append(
                ProjectTask(
                    identifier,
                    project.inner_text().strip(),
                    row.locator(".ws-task-taskname").inner_text().strip(),
                )
            )
        if not tasks:
            raise ValueError("No task rows were found; the previous cache was preserved.")
        return tuple(tasks)

    def _field_id(self, slot: HourSlot) -> str:
        self._assert_origin()
        week = IsoWeek(*slot.work_date.isocalendar()[:2])
        if self._week != week or self.current_week() != week:
            raise ValueError("The browser week changed. Prepare a new preview.")
        return f"{slot.work_date.isoweekday()}-{slot.task.id}-{slot.work_date:%d%m%Y}"

    def read(self, slot: HourSlot) -> str:
        field_id = self._field_id(slot)
        field = self.page.locator(f'input[id="{field_id}"]')
        if field.count() != 1:
            raise ValueError("A date/task slot is missing. Check the Testhuset week and layout.")
        return field.input_value()

    def write_verified(self, slot: HourSlot) -> None:
        field_id = self._field_id(slot)
        field = self.page.locator(f'input[id="{field_id}"]')
        if not field.is_enabled() or not field.is_editable():
            raise ValueError("This Testhuset day is locked. No further slots were changed.")
        cell = field.locator("xpath=ancestor::td[1]")
        if cell.get_attribute("data-kommentarindstilling") == "1":
            raise ValueError("This task requires a comment. Register it manually in Testhuset.")
        # onchange performs the page's own AJAX save; correlate the response to this slot.
        with self.page.expect_response(
            lambda response: (
                urlsplit(response.url).path.lower().endswith("/ajaxupdatetime")
                and urlsplit(response.url).netloc == "testhuset.eazyproject.net"
                and field_id in (response.request.post_data or "")
            )
        ) as pending:
            field.fill(slot.hours)
            field.press("Tab")
        response = pending.value
        if not response.ok:
            raise ValueError(
                "Testhuset save failed. Rescan before retrying; some slots may be saved."
            )
        data = response.json()
        answer = data.get("d", "").split("|") if isinstance(data, dict) else []
        if len(answer) < 3 or answer[0] != "1":
            raise ValueError("Testhuset did not confirm the save. Rescan before retrying.")
        if parse_hours(answer[2]) != parse_hours(slot.hours):
            raise ValueError(
                "Testhuset accepted a different value. Review the week before retrying."
            )
        expect(field).to_have_value(answer[2])


@contextmanager
def temporary_sheet(
    cancelled: Event,
    status: Callable[[str], None],
    *,
    credentials: TesthusetCredentialStore | None = None,
) -> Iterator[WeeklySheet]:
    """All Playwright objects live and close on the calling worker thread."""
    stage = "starting Microsoft Edge"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=False, timeout=30_000)
            try:
                stage = "creating the temporary browser session"
                context = browser.new_context(accept_downloads=False)
                try:
                    page = context.new_page()
                    # Testhuset's weekly sheet can take longer than an ordinary page to update.
                    page.set_default_timeout(45_000)
                    adapter = TesthusetBrowser(page, cancelled)
                    stage = "opening the Testhuset login page"
                    credential = credentials.load() if credentials is not None else None
                    if credential is None:
                        status(
                            "Sign in directly in the temporary Edge window. Leave Remember me off."
                        )
                    else:
                        status("Signing in with the sign-in saved in Windows Credential Manager…")
                    adapter.open(credential)
                    status("Reading the selected Testhuset week…")
                    stage = "reading the Testhuset weekly sheet"
                    yield adapter
                finally:
                    context.close()
            finally:
                browser.close()
    except (AssertionError, Error, OSError) as error:
        # Error text can contain URLs, request contents or entered values.
        # Keep the log privacy-safe.
        _LOG.warning("Testhuset browser failure during %s (%s)", stage, type(error).__name__)
        raise ValueError(_browser_failure_message(stage, error)) from None
