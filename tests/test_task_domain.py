"""Module 2 domain: status mapping, line guardrails, comment fallback and fingerprints."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.domain.bc_models import BCServiceOrderHeader, BCServiceOrderLine
from src.domain.frontu_models import (
    FrontuMaterial,
    FrontuTask,
    FrontuWork,
    hours_to_duration_minutes,
)
from src.domain.mapping import (
    DESCRIPTION_MAX,
    build_unmapped_comment_line,
    filter_unlinked_bc_lines,
    header_hash,
    is_frozen,
    is_line_update_allowed,
    line_hash,
    map_frontu_status,
)


def line(line_no: int, guid: str | None = None, **fields: str) -> BCServiceOrderLine:
    return BCServiceOrderLine.model_validate(
        {
            "DocumentNo": "SO00001",
            "LineNo": line_no,
            "Type": "Item",
            "No": "ITEM-1",
            "Quantity": 2,
            "UnitPrice": 10.5,
            "Frontu_Integration_Line_GUID": guid,
            **fields,
        }
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (100, "INACTIVE"),
        (101, "SYNCED"),
        (102, "UNASSGINED"),
        (110, "REOPEN"),
        (200, "IN PROGRESS"),
        (300, "TASKER STOPPED"),
        (400, "DONE"),
        (401, "DONE WITH NOTE"),
        (402, "APPROVED"),
        (500, "TASKER UNSIGN"),
        (600, "ABORTED"),
        (999, "TASKERYJE"),
        (None, "TASKERYJE"),
    ],
)
def test_frontu_status_maps_to_bc_document_status(code: int | None, expected: str) -> None:
    assert map_frontu_status(code) == expected


@pytest.mark.parametrize("code", [101, 102, 110, 999])
def test_line_updates_allowed(code: int) -> None:
    assert is_line_update_allowed(code) is True


@pytest.mark.parametrize("code", [100, 200, 300, 400, 401, 402, 500, 600])
def test_line_updates_blocked_by_no_frontu_line_updates_or_frozen(code: int) -> None:
    assert is_line_update_allowed(code) is False


@pytest.mark.parametrize(("code", "frozen"), [(402, True), (600, True), (400, False), (101, False)])
def test_frozen_statuses(code: int, frozen: bool) -> None:
    assert is_frozen(code) is frozen


def test_filter_skips_lines_with_frontu_guid() -> None:
    lines = [line(10000), line(20000, "abc-guid"), line(30000, "   "), line(40000, "")]

    unlinked = filter_unlinked_bc_lines(lines)

    assert [item.LineNo for item in unlinked] == [10000, 30000, 40000]


def test_unmapped_material_becomes_comment_line() -> None:
    comment = build_unmapped_comment_line(
        "SO00001", 50000, "MATERIAL", "Oil filter X", Decimal("2.500"), "PCS", "MAT-77"
    )

    assert comment.Type == " "
    assert comment.No is None
    assert comment.Quantity == 0
    assert comment.Description == "Unmapped Frontu material: Oil filter X (2.5 PCS)"
    assert comment.Description_2 == "Frontu code: MAT-77"


def test_unmapped_work_comment_truncated_to_bc_length() -> None:
    comment = build_unmapped_comment_line("SO00001", 60000, "WORK", "x" * 300, Decimal(1), None)

    assert comment.Description is not None
    assert comment.Description.startswith("Unmapped Frontu work: ")
    assert len(comment.Description) == DESCRIPTION_MAX
    assert comment.Description_2 is None


def test_header_hash_is_deterministic_and_ignores_volatile_timestamps() -> None:
    base = {"No": "SO00001", "DocumentStatus": "FRONTU", "Description": "Service"}
    first = BCServiceOrderHeader.model_validate(
        {**base, "SystemModifiedAt": "2026-10-20T10:00:00Z"}
    )
    second = BCServiceOrderHeader.model_validate(
        {**base, "SystemModifiedAt": "2026-10-21T10:00:00Z", "@odata.etag": "W/1"}
    )
    changed = BCServiceOrderHeader.model_validate({**base, "Description": "Other"})

    assert header_hash(first) == header_hash(second)
    assert len(header_hash(first)) == 64
    assert header_hash(first) != header_hash(changed)


def test_line_hash_ignores_guid_but_tracks_content() -> None:
    assert line_hash(line(10000)) == line_hash(line(10000, "guid-1"))
    assert line_hash(line(10000)) != line_hash(line(10000, Description="changed"))


def test_frontu_task_flattens_nested_references() -> None:
    task = FrontuTask.model_validate(
        {
            "guid": "t-1",
            "code": 42,
            "status": 200,
            "client": {"guid": "c-1"},
            "workspace": {"id": "w-1"},
            "user": "u-1",
            "unknownVendorField": True,
        }
    )

    assert (task.code, task.client, task.workspace, task.user) == ("42", "c-1", "w-1", "u-1")
    assert task.systemId is None


def test_frontu_material_uses_amount_and_standartized_material() -> None:
    material = FrontuMaterial.model_validate(
        {
            "guid": "m-1",
            "amount": "1.5",
            "systemId": 7,
            "task": {"guid": "t-1"},
            "standartizedMaterial": "sm-1",
        }
    )

    assert (material.amount, material.systemId, material.task) == (1.5, "7", "t-1")
    assert material.standartizedMaterial == "sm-1"


def test_frontu_work_uses_integer_duration_minutes() -> None:
    work = FrontuWork.model_validate(
        {"systemId": "RES-1", "task": "t-1", "duration": 90, "user": {"guid": "u-1"}}
    )

    assert (work.duration, work.user, work.standartizedWork) == (90, "u-1", None)


def test_material_and_work_require_task_and_system_id() -> None:
    with pytest.raises(ValidationError):
        FrontuMaterial.model_validate({"amount": 1})
    with pytest.raises(ValidationError):
        FrontuWork.model_validate({"task": "t-1", "duration": 1})


@pytest.mark.parametrize(
    ("hours", "minutes"),
    [(Decimal("1.5"), 90), (0.25, 15), (Decimal("0.0083"), 0), (Decimal("0.0084"), 1), (2, 120)],
)
def test_hours_to_duration_minutes_rounds_half_up(hours: Decimal | float, minutes: int) -> None:
    assert hours_to_duration_minutes(hours) == minutes


def test_bc_line_decimals_serialise_as_json_numbers() -> None:
    dumped = line(10000).model_dump(mode="json")

    assert (dumped["Quantity"], dumped["UnitPrice"], dumped["LineAmount"]) == (2.0, 10.5, 0.0)
