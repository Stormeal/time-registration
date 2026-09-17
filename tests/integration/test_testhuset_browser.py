"""Exercise the inspected DOM/save contract in real Edge against an isolated fixture.

Every request is intercepted locally; these tests cannot contact a workplace service.
"""

import json
from datetime import date
from threading import Event

import pytest
from playwright.sync_api import sync_playwright

from qi_flow.application.testhuset import HourSlot, TesthusetCredential
from qi_flow.domain.models import IsoWeek
from qi_flow.domain.testhuset import ProjectTask
from qi_flow.infrastructure.testhuset_browser import TesthusetBrowser

HTML = """<!doctype html><html><body>
<span class="ws-header-week">Uge 38 <span>2026</span></span>
<ul><li title="&lt; Forrige uge" onclick="move(-1)">Previous</li>
<li title="Næste uge &gt;" onclick="move(1)">Next</li></ul>
<input id="tasksearch">
<input type="checkbox" id="ctl00_ContentPlaceHolder1_CheckBoxShowFavorites">
<table id="idTabelUgeseddel"><tbody>
<tr class="ws-header-project" id="IMG-P22"><th>
<div class="ws-toggle-project" onclick="expand(this)"><i class="fa-angle-right"></i>
<span class="ws-header-project-name">Example project</span></div></th></tr>
<tr class="ws-row-task" id="11-22" hidden><td class="ws-task-taskname">Testing</td>
<td data-kommentarindstilling="0"><input id="1-11-22-14092026" value=""
onchange="save(this)"></td></tr></tbody></table>
<button id="close-week" onclick="throw new Error('Must never close week')">Close week</button>
<script>
let week=38;
function move(delta) {
 week+=delta; document.querySelector('.ws-header-week').textContent='Uge '+week+' 2026';
}
function expand(el) {
 el.querySelector('i').className='fa-angle-down';
 document.querySelector('.ws-row-task').hidden=false;
}
async function save(el) {
 const r=await fetch('/weeksheet2.aspx/ajaxupdatetime', {
 method:'POST', body:JSON.stringify({fieldid:el.id,value:el.value})});
 const answer=(await r.json()).d.split('|');
 if(answer[0]==='1') el.value=answer[2];
}
</script></body></html>"""


@pytest.fixture
def browser_sheet():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(1500)
        writes = []
        reply = {"d": "1|Saved|7,75"}

        def intercept(route):
            if route.request.url.endswith("/ajaxupdatetime"):
                writes.append(json.loads(route.request.post_data))
                route.fulfill(json=reply)
            else:
                route.fulfill(content_type="text/html", body=HTML)

        page.route("**/*", intercept)
        page.goto("https://testhuset.eazyproject.net/weeksheet2.aspx")
        adapter = TesthusetBrowser(page, Event())
        yield adapter, writes, reply
        context.close()
        browser.close()


def test_scan_navigates_expands_and_does_not_write(browser_sheet) -> None:
    adapter, writes, _ = browser_sheet
    tasks = adapter.scan(IsoWeek(2026, 37))
    assert adapter.current_week() == IsoWeek(2026, 37)
    assert tasks == (ProjectTask("11-22", "Example project", "Testing"),)
    assert writes == []


def test_open_navigates_directly_to_the_weekly_sheet() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(1500)

        def intercept(route):
            route.fulfill(content_type="text/html", body=HTML)

        page.route("**/*", intercept)
        adapter = TesthusetBrowser(page, Event())
        adapter.open()
        assert page.url.endswith("/weeksheet2.aspx")
        context.close()
        browser.close()


def test_saved_sign_in_is_filled_only_into_visible_login_fields(browser_sheet) -> None:
    adapter, _, _ = browser_sheet
    adapter.page.set_content('<input name="username"><input type="password">')
    adapter.sign_in(TesthusetCredential("consultant@example.test", "secret"))
    assert adapter.page.locator("input[name='username']").input_value() == "consultant@example.test"
    assert adapter.page.locator("input[type='password']").input_value() == "secret"


def test_slot_save_uses_page_response_and_accepts_comma_value(browser_sheet) -> None:
    adapter, writes, _ = browser_sheet
    task = adapter.scan(IsoWeek(2026, 38))[0]
    slot = HourSlot(date(2026, 9, 14), task, "7.75")
    adapter.write_verified(slot)
    assert writes == [{"fieldid": "1-11-22-14092026", "value": "7.75"}]
    assert adapter.read(slot) == "7,75"


@pytest.mark.parametrize("answer", ["2|Rejected|0", "1|Saved|7,50", "1|Saved|7.750"])
def test_failed_or_mismatched_save_is_not_reported_success(browser_sheet, answer: str) -> None:
    adapter, writes, reply = browser_sheet
    reply["d"] = answer
    task = adapter.scan(IsoWeek(2026, 38))[0]
    with pytest.raises(ValueError):
        adapter.write_verified(HourSlot(date(2026, 9, 14), task, "7.75"))
    assert len(writes) == 1


def test_wrong_week_and_filtered_scan_fail_closed(browser_sheet) -> None:
    adapter, writes, _ = browser_sheet
    task = adapter.scan(IsoWeek(2026, 38))[0]
    with pytest.raises(ValueError, match="week changed"):
        adapter.read(HourSlot(date(2026, 9, 21), task, "7.75"))
    adapter.page.route(
        "**/weeksheet2.aspx",
        lambda route: route.fulfill(
            content_type="text/html",
            body=HTML.replace('type="checkbox"', 'type="checkbox" checked'),
        ),
    )
    with pytest.raises(ValueError, match="Turn off"):
        adapter.scan(IsoWeek(2026, 38))
    assert not writes
