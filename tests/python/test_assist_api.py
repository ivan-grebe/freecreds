from __future__ import annotations

import httpx
import pytest
import respx

from freecreds import assist_api


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
