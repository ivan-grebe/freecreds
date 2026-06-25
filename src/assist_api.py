"""HTTP client for the public ASSIST API.

These are the unauthenticated endpoints the assist.org website itself uses.
The documented endpoints under `/apidocs/` that require an API key (notably
`/AcademicYears/api`) are NOT used here — public key access opens around
October 2026 per ASSIST. The client infers the latest usable year from
published agreement metadata instead.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://prod.assistng.org"
USER_AGENT = "FreeCreds/0.1 (+https://github.com/ivan-grebe/freecreds)"
DEFAULT_THROTTLE_S = 0.4  # min gap between requests
DEFAULT_TIMEOUT_S = 30.0
MAX_RETRIES = 3


class AssistAPIError(RuntimeError):
    pass


class AssistClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        throttle_s: float = DEFAULT_THROTTLE_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ):
        self.base_url = base_url.rstrip("/")
        self.throttle_s = throttle_s
        self._last_request_at: float = 0.0
        self._client = httpx.Client(
            timeout=timeout_s,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AssistClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _throttle(self) -> None:
        now = time.monotonic()
        gap = now - self._last_request_at
        if gap < self.throttle_s:
            time.sleep(self.throttle_s - gap)
        self._last_request_at = time.monotonic()

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        delay = 1.0
        last_error = "no response"
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self._client.get(url)
            except httpx.HTTPError as e:
                last_error = str(e)
                log.warning("Network error on %s (attempt %d): %s", url, attempt, e)
            else:
                if resp.status_code == 429:
                    raise AssistAPIError(f"429 rate-limited at {url}; stopping.")
                if 500 <= resp.status_code < 600:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    log.warning("Server %d on %s (attempt %d)", resp.status_code, url, attempt)
                elif resp.status_code >= 400:
                    raise AssistAPIError(f"{resp.status_code} on {url}: {resp.text[:200]}")
                else:
                    try:
                        return resp.json()
                    except ValueError as e:
                        raise AssistAPIError(f"Non-JSON response from {url}: {e}") from e
            if attempt < MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
        raise AssistAPIError(f"Exhausted retries for {url}: {last_error}")

    @staticmethod
    def _unwrap_result(payload: Any, url_hint: str) -> Any:
        """Endpoints under /articulation/api/ wrap responses in
        {result, validationFailure, isSuccessful}. Others return raw lists.
        """
        if isinstance(payload, dict) and "isSuccessful" in payload:
            if not payload.get("isSuccessful"):
                raise AssistAPIError(
                    f"API call failed for {url_hint}: {payload.get('validationFailure')}"
                )
            return payload.get("result")
        return payload

    # --- Endpoints ---

    def get_institutions(self) -> List[Dict[str, Any]]:
        data = self._get("/Institutions/api")
        return self._unwrap_result(data, "/Institutions/api") or []

    def get_agreements_from(self, institution_id: int) -> List[Dict[str, Any]]:
        """List institutions that have published agreements *to* the given
        receiving institution, with the academic year IDs in which those
        agreements are published.
        """
        path = f"/articulation/api/Agreements/Published/from/{institution_id}"
        return self._unwrap_result(self._get(path), path) or []

    def list_agreement_keys(
        self,
        receiving_id: int,
        sending_id: int,
        year_id: int,
        types: str = "Department",
    ) -> Dict[str, Any]:
        """Return per-{type} agreement keys and the "All{type}s" summary key
        between the two institutions for the given academic year.

        Use `types="Department"` for CSU/UC targets (the norm) and
        `types="Major"` for AICCU/private targets that only publish at the
        major level. The response shape is identical; only the report
        `type` values differ ("AllDepartments" vs "AllMajors").
        """
        path = (
            f"/articulation/api/Agreements/Published/for/{receiving_id}"
            f"/to/{sending_id}/in/{year_id}?types={types}"
        )
        return self._unwrap_result(self._get(path), path) or {}

    def get_agreement(self, key: str) -> Dict[str, Any]:
        """Fetch a full agreement payload by key
        (e.g. "74/110/to/7/AllDepartments").
        """
        path = f"/articulation/api/Agreements?Key={quote(key, safe='')}"
        return self._unwrap_result(self._get(path), path) or {}


# --- Helpers built atop the client ---

def latest_academic_year_id(client: AssistClient, reference_institution_id: int) -> int:
    """Infer the most recent academic year ID where at least one CCC has
    a published agreement to the target.

    We only consider `sendingYearIds` (years each CCC has published agreements
    TO this target) — not `receivingYearIds` (years the target has published
    its own side), because the ingester filters CCCs by `sendingYearIds`
    containing the chosen year. Using the target's `receivingYearIds` would
    yield a year where no CCC actually has agreements ready, producing an
    empty filter result — common for AICCU targets whose CCC partners lag on
    republishing.
    """
    entries = client.get_agreements_from(reference_institution_id)
    max_id = 0
    for e in entries:
        for y in e.get("sendingYearIds") or []:
            if isinstance(y, int) and y > max_id:
                max_id = y
    if not max_id:
        raise AssistAPIError(
            f"No CCC has a published sendingYearIds for institution {reference_institution_id}"
        )
    return max_id


def find_institution_by_code(
    institutions: List[Dict[str, Any]], code: str
) -> Dict[str, Any]:
    target = code.strip().upper()
    for inst in institutions:
        if (inst.get("code") or "").strip().upper() == target:
            return inst
    raise KeyError(f"No institution with ASSIST code {code!r}")


def institution_display_name(inst: Dict[str, Any], year: Optional[int] = None) -> str:
    names = inst.get("names") or []
    if not names:
        return (inst.get("code") or "?").strip()
    if year is not None:
        candidates = [n for n in names if (n.get("fromYear") or 0) <= year]
        if candidates:
            return candidates[-1].get("name") or (inst.get("code") or "?").strip()
    return names[-1].get("name") or (inst.get("code") or "?").strip()


# --- Smoke test entry point ---

def _smoke() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with AssistClient() as c:
        insts = c.get_institutions()
        print(f"Got {len(insts)} institutions")
        csuf = find_institution_by_code(insts, "CSUFULL")
        print("CSUF record:")
        print(f"  id={csuf['id']} code={csuf['code']!r}")
        print(f"  name={institution_display_name(csuf)}")
        print(f"  category={csuf.get('category')} termType={csuf.get('termType')}")

        year_id = latest_academic_year_id(c, csuf["id"])
        print(f"Inferred latest academic year ID: {year_id}")

        ccs = c.get_agreements_from(csuf["id"])
        active = [
            e for e in ccs
            if year_id in (e.get("sendingYearIds") or [])
            and (e.get("receivingInstitution") or {}).get("isCommunityCollege")
        ]
        print(f"CCCs with current agreements to CSUF: {len(active)}")

        if active:
            sample_cc = active[0]["receivingInstitution"]
            print(f"  example: {sample_cc['code'].strip()} (id={sample_cc['id']})")
            keys = c.list_agreement_keys(csuf["id"], sample_cc["id"], year_id)
            reports = keys.get("allReports") or keys.get("reports") or []
            all_dep = next((r for r in reports if r.get("type") == "AllDepartments"), None)
            print(f"  AllDepartments key: {all_dep['key'] if all_dep else '(not found)'}")


if __name__ == "__main__":
    _smoke()
