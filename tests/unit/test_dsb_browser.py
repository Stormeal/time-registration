"""Privacy-safe DSB browser diagnostics."""

from threading import Event

import pytest
from playwright.sync_api import Error as PlaywrightError

from qi_flow.domain.testhuset import ProjectTask
from qi_flow.infrastructure.dsb_browser import (
    MAX_SSO_TAB_HANDOFFS,
    SSO_POLL_MILLISECONDS,
    allocation_id,
    allocation_name_column,
    hour_value_matches,
    is_dsb_portal,
    is_send_confirmation,
    is_target_closed,
    safe_page_location,
    should_retry_sso_handoff,
    wait_for_bemandinger,
)


class _BemandingerLocator:
    def __init__(self, visible_after: int) -> None:
        self.first = self
        self._visible_after = visible_after
        self._checks = 0

    def is_visible(self) -> bool:
        self._checks += 1
        return self._checks > self._visible_after


class _SsoPage:
    def __init__(self, visible_after: int) -> None:
        self.locator = _BemandingerLocator(visible_after)
        self.waits: list[int] = []

    def get_by_text(self, _pattern):
        return self.locator

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


def test_safe_page_location_removes_sso_query_and_fragment() -> None:
    assert (
        safe_page_location(
            "https://login.microsoftonline.com/tenant/saml2?code=secret&state=private#token"
        )
        == "https://login.microsoftonline.com/tenant/saml2"
    )


def test_dsb_portal_detection_requires_the_dsb_host() -> None:
    assert is_dsb_portal("https://dsbep0u.kmd.dsb.dk:44351/sap/bc/ui2/flp")
    assert not is_dsb_portal("https://login.microsoftonline.com/tenant/saml2")


def test_target_closed_detection_does_not_treat_other_browser_errors_as_recoverable() -> None:
    target_closed = type("TargetClosedError", (PlaywrightError,), {})("closed")

    assert is_target_closed(target_closed)
    assert not is_target_closed(PlaywrightError("other browser error"))


def test_sso_handoff_retries_only_expected_tab_closures_within_a_bound() -> None:
    target_closed = type("TargetClosedError", (PlaywrightError,), {})("closed")

    assert should_retry_sso_handoff(target_closed, 0)
    assert should_retry_sso_handoff(target_closed, MAX_SSO_TAB_HANDOFFS - 1)
    assert not should_retry_sso_handoff(target_closed, MAX_SSO_TAB_HANDOFFS)
    assert not should_retry_sso_handoff(PlaywrightError("other browser error"), 0)


def test_wait_for_bemandinger_allows_time_for_user_managed_sso() -> None:
    page = _SsoPage(visible_after=2)

    assert wait_for_bemandinger(page, Event(), polls=3)
    assert page.waits == [SSO_POLL_MILLISECONDS, SSO_POLL_MILLISECONDS]


def test_allocation_name_column_uses_the_named_header_not_a_fixed_position() -> None:
    assert allocation_name_column(["", "", "Navn", "Korttekst"]) == 2


def test_allocation_name_column_fails_closed_when_the_sap_layout_changes() -> None:
    with pytest.raises(ValueError, match="layout changed"):
        allocation_name_column(["", "Kode", "Korttekst"])


def test_allocation_id_is_stable_and_compatible_with_the_shared_task_contract() -> None:
    identifier = allocation_id("Allocation A")

    assert identifier == allocation_id("Allocation A")
    assert identifier != allocation_id("Allocation B")
    assert ProjectTask(identifier, "DSB", "Allocation A").id == identifier


@pytest.mark.parametrize(
    "message",
    ["Data er sendt", "Timesheet saved", "Registreret med succes"],
)
def test_send_confirmation_requires_an_explicit_success_term(message: str) -> None:
    assert is_send_confirmation(message)


@pytest.mark.parametrize("message", ["Data could not be sent", "Validation failed", "Send"])
def test_send_confirmation_rejects_non_confirmation_messages(message: str) -> None:
    assert not is_send_confirmation(message)


@pytest.mark.parametrize("actual", ["10,00", "10.00", "10"])
def test_hour_value_matches_accepts_dsb_and_qi_flow_decimal_formats(actual: str) -> None:
    assert hour_value_matches(actual, "10.00")


def test_hour_value_matches_rejects_invalid_or_different_hours() -> None:
    assert not hour_value_matches("not a number", "10.00")
    assert not hour_value_matches("9,50", "10.00")
