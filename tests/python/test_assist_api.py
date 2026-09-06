from __future__ import annotations

from itertools import permutations

import httpx
import pytest
import respx

from freecreds import assist_api


@pytest.mark.parametrize("names", list(permutations([
    {"name": "Compton College", "fromYear": 2019},
    {"name": "El Camino College", "fromYear": 2006},
    {"name": "Compton Community College", "fromYear": 1927},
])))
def test_institution_name_uses_effective_year_not_response_order(names):
    institution = {"code": "COMPTON", "names": list(names)}
    assert assist_api.institution_display_name(institution) == "Compton College"
    assert assist_api.institution_display_name(institution, 2019) == "Compton College"
    assert assist_api.institution_display_name(institution, 2018) == "El Camino College"
    assert assist_api.institution_display_name(institution, 2005) == "Compton Community College"
    assert assist_api.institution_display_name(institution, 1900) == "COMPTON"


def test_institution_name_handles_missing_history_and_undated_names():
    assert assist_api.institution_display_name({"code": " TEST "}) == "TEST"
    assert assist_api.institution_display_name({"names": []}) == "?"
    assert assist_api.institution_display_name({"names": [
        {"name": "Current", "fromYear": 2020},
        {"name": "Undated", "fromYear": None},
    ]}) == "Current"


@respx.mock
def test_client_retries_server_errors(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(assist_api.time, "sleep", lambda _seconds: None)
    route = respx.get("https://assist.test/retry").mock(
        side_effect=[
            httpx.Response(503, text="temporarily unavailable"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    with assist_api.AssistClient(base_url="https://assist.test", throttle_s=0) as client:
        assert client._get("/retry") == {"ok": True}

    assert route.call_count == 2


@respx.mock
def test_client_stops_immediately_when_rate_limited():
    route = respx.get("https://assist.test/limited").mock(
        return_value=httpx.Response(429, text="slow down")
    )

    with (
        assist_api.AssistClient(base_url="https://assist.test", throttle_s=0) as client,
        pytest.raises(assist_api.AssistAPIError, match="rate-limited"),
    ):
        client._get("/limited")

    assert route.call_count == 1


def test_client_rejects_unsuccessful_wrapped_payload():
    with pytest.raises(assist_api.AssistAPIError, match="not published"):
        assist_api.AssistClient._unwrap_result(
            {
                "isSuccessful": False,
                "validationFailure": "not published",
                "result": None,
            },
            "/agreement",
        )
