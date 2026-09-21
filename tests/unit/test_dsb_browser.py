"""Privacy-safe DSB browser diagnostics."""

from playwright.sync_api import Error as PlaywrightError

from qi_flow.infrastructure.dsb_browser import is_dsb_portal, is_target_closed, safe_page_location


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
