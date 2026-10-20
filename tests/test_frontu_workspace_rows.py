"""Frontu listing rows in the live shape: guid identifiers and fail-closed validation."""

import httpx
import pytest

from src.adapters.resilience import ExternalServiceError
from tests.fakes import FakeClock, RecordingHandler, frontu_client


def _paged(pages: list[httpx.Response]) -> RecordingHandler:
    return RecordingHandler(lambda request: pages[int(request.url.params["page"]) - 1])


def _real_row(guid: str | None, serial: str | None, **extra: object) -> dict[str, object]:
    """Shape inspected on the live API: `guid`, no `id`, many vendor fields."""
    row: dict[str, object] = {"guid": guid, "systemId": None, "serialNumber": serial}
    row.update({"type": "object", "title": "T", "warrantyRequired": False, "tags": []}, **extra)
    return row


@pytest.mark.asyncio
async def test_live_envelope_with_guid_identifiers_is_parsed() -> None:
    body = {"items": [_real_row("6f1c-uuid", "VIN-1", id=7)], "next_page": 0}
    mock = _paged([httpx.Response(200, json=body)])

    [found] = await frontu_client(mock, FakeClock()).list_workspaces()

    assert (found.id, found.systemId, found.serialNumber) == ("6f1c-uuid", None, "VIN-1")


@pytest.mark.parametrize(
    "row",
    [
        _real_row(None, "VIN-1"),
        _real_row("6f1c-uuid", None, systemId={"unexpected": "object"}),
        _real_row("6f1c-uuid", "VIN-1", serialNumber=["not", "a", "string"]),
    ],
    ids=["serial-without-guid", "guid-with-bad-field", "unreadable-serial"],
)
@pytest.mark.asyncio
async def test_unreadable_identifiable_row_aborts_the_listing(row: dict[str, object]) -> None:
    mock = _paged([httpx.Response(200, json={"items": [_real_row("ok", "VIN-0"), row]})])

    with pytest.raises(ExternalServiceError, match="partial identity index"):
        await frontu_client(mock, FakeClock()).list_workspaces()


@pytest.mark.asyncio
async def test_unidentifiable_rows_are_skipped_with_one_summary_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rows = [_real_row("ok", "VIN-0"), {"title": "no keys"}, "garbage", {"guid": "", "x": 1}]
    mock = _paged([httpx.Response(200, json={"items": rows, "next_page": 0})])

    with caplog.at_level("WARNING", logger="src.adapters.frontu_payloads"):
        found = await frontu_client(mock, FakeClock()).list_workspaces()

    assert [w.id for w in found] == ["ok"]
    assert [r.getMessage() for r in caplog.records] == [
        "Skipped 3 unidentifiable Frontu workspace rows on page 1"
    ]
