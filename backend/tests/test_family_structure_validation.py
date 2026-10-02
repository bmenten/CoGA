import pytest
from fastapi import HTTPException

from backend.app.schemas import FamilyStructureUpdate
from backend.app.services.family_structure_service import (
    _relationship_rows_from_payload,
    _validate_relationship_graph,
)


def _member(sample_id: str, sex: str) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "sex": sex,
        "active": True,
        "sample_uuid": sample_id,
    }


def test_relationship_graph_rejects_parent_sex_mismatch() -> None:
    members = {
        "father": _member("FATHER", "female"),
        "child": _member("CHILD", "male"),
    }

    with pytest.raises(HTTPException) as exc:
        _validate_relationship_graph(
            [
                {
                    "relationship_type": "parent_child",
                    "sample_id_a": "FATHER",
                    "sample_id_b": "CHILD",
                    "role_a": "father",
                    "role_b": "child",
                }
            ],
            target_members=members,
        )

    assert exc.value.status_code == 400
    assert "cannot be recorded as father" in str(exc.value.detail)


def test_relationship_graph_rejects_circular_parent_child_links() -> None:
    members = {
        "grandparent": _member("GRANDPARENT", "male"),
        "parent": _member("PARENT", "female"),
        "child": _member("CHILD", "male"),
    }

    with pytest.raises(HTTPException) as exc:
        _validate_relationship_graph(
            [
                {
                    "relationship_type": "parent_child",
                    "sample_id_a": "GRANDPARENT",
                    "sample_id_b": "PARENT",
                    "role_a": "father",
                    "role_b": "child",
                },
                {
                    "relationship_type": "parent_child",
                    "sample_id_a": "PARENT",
                    "sample_id_b": "CHILD",
                    "role_a": "mother",
                    "role_b": "child",
                },
                {
                    "relationship_type": "parent_child",
                    "sample_id_a": "CHILD",
                    "sample_id_b": "GRANDPARENT",
                    "role_a": "father",
                    "role_b": "child",
                },
            ],
            target_members=members,
        )

    assert exc.value.status_code == 400
    assert "cycle" in str(exc.value.detail)


def _relative(related_to: str, member: str) -> dict[str, object]:
    return {
        "relationship_type": "relative",
        "sample_id_a": related_to,
        "sample_id_b": member,
        "role_a": "relative",
        "role_b": "relative",
        "source": "manual",
        "metadata": {},
    }


def _couple_with_index() -> dict[str, dict[str, object]]:
    return {
        "father": _member("FATHER", "male"),
        "mother": _member("MOTHER", "female"),
        "embryo": _member("EMBRYO", "und"),
        "index": _member("INDEX", "female"),
    }


def _parent_child(parent: str, child: str, role: str) -> dict[str, object]:
    return {
        "relationship_type": "parent_child",
        "sample_id_a": parent,
        "sample_id_b": child,
        "role_a": role,
        "role_b": "child",
    }


def test_relationship_graph_accepts_a_relative_of_unknown_degree() -> None:
    _validate_relationship_graph(
        [
            _parent_child("FATHER", "EMBRYO", "father"),
            _parent_child("MOTHER", "EMBRYO", "mother"),
            _relative("MOTHER", "INDEX"),
        ],
        target_members=_couple_with_index(),
    )


@pytest.mark.parametrize(
    ("relationships", "detail"),
    [
        ([_relative("INDEX", "INDEX")], "related to themselves"),
        ([_relative("MOTHER", "INDEX"), _relative("INDEX", "MOTHER")], "Duplicate relative link"),
        (
            [_parent_child("MOTHER", "EMBRYO", "mother"), _relative("MOTHER", "EMBRYO")],
            "already linked as parent and child",
        ),
    ],
)
def test_relationship_graph_rejects_a_relative_link_that_adds_nothing(
    relationships: list[dict[str, object]], detail: str
) -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_relationship_graph(relationships, target_members=_couple_with_index())

    assert exc.value.status_code == 400
    assert detail in str(exc.value.detail)


def test_a_structure_update_without_relatives_keeps_them_and_with_them_replaces_them() -> None:
    members = _couple_with_index()
    current = [_parent_child("MOTHER", "EMBRYO", "mother"), _relative("MOTHER", "INDEX")]

    def rows(relationships: dict[str, object]) -> list[tuple[object, ...]]:
        update = FamilyStructureUpdate.model_validate({"relationships": relationships})
        return sorted(
            (row["relationship_type"], row["sample_id_a"], row["sample_id_b"])
            for row in _relationship_rows_from_payload(
                update, target_members=members, current_relationships=current
            )
        )

    parent_child = [{"parent": "MOTHER", "child": "EMBRYO", "parent_role": "mother"}]
    # An editor that does not send the links of unknown degree keeps them.
    assert rows({"parent_child": parent_child}) == [
        ("parent_child", "MOTHER", "EMBRYO"),
        ("relative", "MOTHER", "INDEX"),
    ]
    assert rows({"parent_child": parent_child, "relatives": [{"member": "INDEX", "related_to": "FATHER"}]}) == [
        ("parent_child", "MOTHER", "EMBRYO"),
        ("relative", "FATHER", "INDEX"),
    ]
    assert rows({"parent_child": parent_child, "relatives": []}) == [("parent_child", "MOTHER", "EMBRYO")]
