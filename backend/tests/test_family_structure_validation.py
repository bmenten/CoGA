import pytest
from fastapi import HTTPException

from backend.app.services.family_structure_service import _validate_relationship_graph


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
