"""Exercise DSB row targeting in a local, request-blocked Edge page."""

from datetime import UTC, date, datetime
from threading import Event

import pytest
from playwright.sync_api import sync_playwright

from qi_flow.application.dsb import DsbService
from qi_flow.application.dto import ManualWorkSessionCommand
from qi_flow.application.testhuset import FillDecision, HourSlot
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import IsoWeek
from qi_flow.domain.testhuset import ProjectTask
from qi_flow.infrastructure.dsb_browser import DsbBrowser, allocation_id
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator
from qi_flow.infrastructure.testhuset_cache import JsonTaskCache


def _slot(day: date, allocation: str, hours: str = "3.00") -> HourSlot:
    return HourSlot(day, ProjectTask(allocation_id(allocation), "DSB", allocation), hours)


def _row(identifier: str, allocation: str, hours: str) -> str:
    return (
        f'<tr class="sapMListTblRow" id="{identifier}">'
        f'<td><input role="combobox" value="{allocation}"></td>'
        f'<td><input type="text" role="spinbutton" value="{hours}" '
        f'data-saved-value="{hours}" onchange="save(this)"></td></tr>'
    )


def _day(heading: str, rows: str) -> str:
    return f'<tr class="sapMGHLI"><td class="sapMGHLITitle">{heading}</td></tr>{rows}'


def _html(days: str, *, monday: str = "20260914", sunday: str = "20260920") -> str:
    return f"""<!doctype html><html><body>
    <button role="tab">Oversigt</button>
    <div data-sap-day="{monday}" aria-selected="false" onclick="selectWeek()">Monday</div>
    <div data-sap-day="{sunday}" aria-selected="false">Sunday</div>
    <button onclick="document.querySelector('#entries').hidden=false">Indtast datarecords</button>
    <table id="entries" hidden><tbody>{days}</tbody></table>
    <button id="application-TimeEntry-manageTimesheet-component---worklist--OverviewSubmitButton"
      onclick="window.sendCount++">Send</button>
    <button id="approve" onclick="window.approveCount++">Approve</button>
    <script>
    window.saved=[]; window.sendCount=0; window.approveCount=0;
    function selectWeek() {{
      document.querySelectorAll('[data-sap-day]').forEach(day =>
        day.setAttribute('aria-selected','true'));
    }}
    function save(field) {{
      field.dataset.savedValue=field.value;
      window.saved.push({{row:field.closest('tr').id, value:field.value}});
    }}
    </script></body></html>"""


@pytest.fixture
def edge_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(1500)
        page.route("**/*", lambda route: route.abort())
        try:
            yield page
        finally:
            context.close()
            browser.close()


def _open(page, html: str) -> DsbBrowser:
    page.set_content(html)
    return DsbBrowser(page, Event())


def test_read_and_write_choose_allocation_b_without_changing_a(edge_page) -> None:
    day = _day(
        "Mandag 14. september 2026",
        _row("allocation-a", "Allocation A", "4,00") + _row("allocation-b", "Allocation B", "2,00"),
    )
    adapter = _open(edge_page, _html(day))
    slot = _slot(date(2026, 9, 14), "Allocation B")

    assert adapter.read(slot) == "2.00"
    adapter.write_verified(slot)

    assert edge_page.locator("#allocation-a input[role=combobox]").input_value() == "Allocation A"
    assert (
        edge_page.locator("#allocation-a input[role=spinbutton]").get_attribute("data-saved-value")
        == "4,00"
    )
    assert (
        edge_page.locator("#allocation-b input[role=spinbutton]").get_attribute("data-saved-value")
        == "3,00"
    )
    assert edge_page.evaluate("window.saved") == [{"row": "allocation-b", "value": "3,00"}]


def test_missing_allocation_refuses_without_repurposing_another_row(edge_page) -> None:
    adapter = _open(
        edge_page,
        _html(_day("Mandag 14. september 2026", _row("allocation-a", "Allocation A", "4,00"))),
    )
    slot = _slot(date(2026, 9, 14), "Allocation B")

    with pytest.raises(ValueError, match=r"allocation.*row|row.*allocation"):
        adapter.read(slot)
    with pytest.raises(ValueError, match=r"allocation.*row|row.*allocation"):
        adapter.write_verified(slot)

    assert edge_page.locator("#allocation-a input[role=combobox]").input_value() == "Allocation A"
    assert edge_page.evaluate("window.saved") == []
    assert edge_page.evaluate("window.sendCount") == 0


def test_missing_date_refuses_even_when_allocation_exists_on_another_day(edge_page) -> None:
    days = _day("Tirsdag 15. september 2026", _row("other-day", "Allocation B", "2,00"))
    adapter = _open(edge_page, _html(days))
    slot = _slot(date(2026, 9, 14), "Allocation B")

    with pytest.raises(ValueError, match="date row is missing"):
        adapter.read(slot)
    with pytest.raises(ValueError, match="date row is missing"):
        adapter.write_verified(slot)

    assert edge_page.evaluate("window.saved") == []
    assert edge_page.evaluate("window.sendCount") == 0


@pytest.mark.parametrize("duplicate", ["allocation", "date"])
def test_duplicate_target_identity_refuses_without_writing_or_sending(edge_page, duplicate) -> None:
    row = _row("allocation-b-1", "Allocation B", "2,00")
    if duplicate == "allocation":
        days = _day(
            "Mandag 14. september 2026", row + _row("allocation-b-2", "Allocation B", "1,00")
        )
    else:
        days = _day("Mandag 14. september 2026", row) + _day(
            "Mandag 14. september 2026", _row("allocation-b-2", "Allocation B", "1,00")
        )
    adapter = _open(edge_page, _html(days))
    slot = _slot(date(2026, 9, 14), "Allocation B")

    with pytest.raises(ValueError, match=r"multiple|ambiguous|duplicate"):
        adapter.read(slot)
    with pytest.raises(ValueError, match=r"multiple|ambiguous|duplicate"):
        adapter.write_verified(slot)

    assert edge_page.evaluate("window.saved") == []
    assert edge_page.evaluate("window.sendCount") == 0


def test_iso_year_boundary_uses_full_calendar_date(edge_page) -> None:
    days = _day("Torsdag 31. december 2026", _row("december", "Allocation B", "4,00"))
    days += _day("Fredag 1. januar 2027", _row("january", "Allocation B", "2,00"))
    adapter = _open(edge_page, _html(days, monday="20261228", sunday="20270103"))

    assert adapter.read(_slot(date(2027, 1, 1), "Allocation B")) == "2.00"


def test_allocation_changed_after_preview_refuses_write(edge_page) -> None:
    days = _day("Mandag 14. september 2026", _row("allocation-b", "Allocation B", "2,00"))
    adapter = _open(edge_page, _html(days))
    slot = _slot(date(2026, 9, 14), "Allocation B")
    assert adapter.read(slot) == "2.00"

    edge_page.locator("#allocation-b input[role=combobox]").fill("Allocation A")
    with pytest.raises(ValueError, match=r"allocation.*row|row.*allocation"):
        adapter.write_verified(slot)

    assert edge_page.evaluate("window.saved") == []
    assert edge_page.evaluate("window.sendCount") == 0


def test_rejected_reordering_during_fill_never_sends(edge_page, tmp_path, monkeypatch) -> None:
    import qi_flow.infrastructure.dsb_browser as module

    monkeypatch.setattr(module, "SEND_CONFIRMATION_MILLISECONDS", 500)
    monkeypatch.setattr(module, "HOUR_COMMIT_MILLISECONDS", 300)
    days = _day(
        "Mandag 14. september 2026",
        _row("allocation-a", "Allocation A", "3,00") + _row("allocation-b", "Allocation B", "2,00"),
    )
    adapter = _open(edge_page, _html(days))
    intercepted: list[str] = []

    def reject(route):
        intercepted.append(route.request.post_data or "")
        route.fulfill(status=409, json={"accepted": False})

    edge_page.route("https://fixture.invalid/save", reject)
    edge_page.evaluate(
        """() => {
          window.save = field => {
            fetch('https://fixture.invalid/save', {method:'POST', body:field.value});
            field.value = '2,00';
            field.dataset.savedValue = '2,00';
            const row = field.closest('tr');
            row.parentNode.insertBefore(row, document.querySelector('#allocation-a'));
          };
        }"""
    )

    database = SQLiteDatabase(tmp_path / "time.sqlite3")
    database.initialize()
    ids = UuidIdentifierGenerator()

    class Clock:
        def now(self) -> datetime:
            return datetime(2026, 11, 1, tzinfo=UTC)

    def uow() -> SQLiteUnitOfWork:
        return SQLiteUnitOfWork(database)

    cache = JsonTaskCache(tmp_path / "dsb-allocations.json")
    slot = _slot(date(2026, 9, 14), "Allocation B")
    cache.replace((slot.task,))
    tracking = TimeTrackingApplicationService(uow, Clock(), ids)
    dsb = DsbService(uow, Clock(), ids, cache)
    dsb.set_default(slot.task.id)
    tracking.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 14, 7, tzinfo=UTC), datetime(2026, 9, 14, 10, tzinfo=UTC)
        )
    )
    preview = dsb.preview(adapter, IsoWeek(2026, 38))

    with pytest.raises(ValueError, match="did not accept"):
        dsb.fill(adapter, preview, {0: FillDecision.REPLACE}, confirmed=True)

    edge_page.wait_for_timeout(50)
    assert intercepted == ["3,00"]
    assert edge_page.locator("tr.sapMListTblRow").first.get_attribute("id") == "allocation-b"
    assert edge_page.locator("#allocation-a input[role=spinbutton]").input_value() == "3,00"
    assert edge_page.locator("#allocation-b input[role=spinbutton]").input_value() == "2,00"
    assert (
        edge_page.locator("#allocation-b input[role=spinbutton]").get_attribute("data-saved-value")
        == "2,00"
    )
    assert edge_page.evaluate("window.sendCount") == 0
    assert edge_page.evaluate("window.approveCount") == 0


def test_accepted_reordering_verifies_the_reviewed_allocation(edge_page, monkeypatch) -> None:
    import qi_flow.infrastructure.dsb_browser as module

    monkeypatch.setattr(module, "HOUR_COMMIT_MILLISECONDS", 300)
    days = _day(
        "Mandag 14. september 2026",
        _row("allocation-a", "Allocation A", "4,00") + _row("allocation-b", "Allocation B", "2,00"),
    )
    adapter = _open(edge_page, _html(days))
    edge_page.evaluate(
        """() => {
          const save = window.save;
          window.save = field => {
            save(field);
            const row = field.closest('tr');
            row.parentNode.insertBefore(row, document.querySelector('#allocation-a'));
          };
        }"""
    )
    slot = _slot(date(2026, 9, 14), "Allocation B")
    assert adapter.read(slot) == "2.00"

    adapter.write_verified(slot)

    assert edge_page.locator("tr.sapMListTblRow").first.get_attribute("id") == "allocation-b"
    assert edge_page.locator("#allocation-a input[role=spinbutton]").input_value() == "4,00"
    assert edge_page.locator("#allocation-b input[role=spinbutton]").input_value() == "3,00"
    assert (
        edge_page.locator("#allocation-b input[role=spinbutton]").get_attribute("data-saved-value")
        == "3,00"
    )
    assert edge_page.evaluate("window.saved") == [{"row": "allocation-b", "value": "3,00"}]


def test_send_without_confirmation_is_uncertain_and_does_not_approve(
    edge_page, monkeypatch
) -> None:
    import qi_flow.infrastructure.dsb_browser as module

    monkeypatch.setattr(module, "SEND_CONFIRMATION_MILLISECONDS", 500)
    days = _day("Mandag 14. september 2026", _row("allocation-b", "Allocation B", "2,00"))
    adapter = _open(edge_page, _html(days))
    slot = _slot(date(2026, 9, 14), "Allocation B")
    assert adapter.read(slot) == "2.00"
    adapter.write_verified(slot)

    with pytest.raises(ValueError, match="send confirmation"):
        adapter.commit_verified()

    assert edge_page.evaluate("window.saved") == [{"row": "allocation-b", "value": "3,00"}]
    assert edge_page.evaluate("window.sendCount") == 1
    assert edge_page.evaluate("window.approveCount") == 0
