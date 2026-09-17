"""Privacy-safe error messages for the temporary Testhuset browser."""

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from qi_flow.infrastructure.testhuset_browser import _browser_failure_message


def test_timeout_identifies_stage_without_exposing_error_contents() -> None:
    message = _browser_failure_message(
        "reading the Testhuset weekly sheet", PlaywrightTimeoutError("password=secret")
    )

    assert "reading the Testhuset weekly sheet" in message
    assert "secret" not in message


def test_permission_error_explains_browser_start_restriction() -> None:
    message = _browser_failure_message("starting Microsoft Edge", PermissionError("denied"))

    assert "Windows security" in message
    assert "denied" not in message


def test_unexpected_browser_error_is_sanitized_but_actionable() -> None:
    message = _browser_failure_message(
        "creating the temporary browser session", OSError("token=abc")
    )

    assert "creating the temporary browser session" in message
    assert "token=abc" not in message


def test_weekly_sheet_assertion_identifies_the_failing_stage() -> None:
    message = _browser_failure_message("opening the Testhuset login page", AssertionError())

    assert "weekly sheet" in message
    assert "opening the Testhuset login page" in message
