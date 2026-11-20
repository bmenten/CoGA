import pytest
from fastapi import HTTPException

from backend.app.schemas import FamilyStructureUpdate
from backend.app.services.family_structure_service import (
    _link_payload,
    _relationship_key,
    _relationship_rows_from_payload,
    _validate_relationship_graph,
    refuse_package_same_individual_conflicts,
    same_individual_groups,
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


# --- two samples of one person ---------------------------------------------------------------


def _same(first: str, second: str) -> dict[str, object]:
    return {
        "relationship_type": "same_individual",
        "sample_id_a": first,
        "sample_id_b": second,
        "role_a": None,
        "role_b": None,
        "source": "manual",
        "metadata": {},
    }


def _pregnancy(**second_plasma: object) -> dict[str, dict[str, object]]:
    """A monogenic NIPT's father, plasma and fetus, the conventional NIPT's plasma under another
    sample ID, and two grandfathers."""
    plasma = {"clinical_status": "unaffected", "carrier_status": "carrier"}
    return {
        "father": _member("FATHER", "male"),
        "plasma1": {**_member("PLASMA1", "female"), **plasma},
        "fetus": _member("FETUS", "und"),
        "plasma2": {**_member("PLASMA2", "female"), **second_plasma},
        "plasma3": _member("PLASMA3", "und"),
        "grandfather1": _member("GRANDFATHER1", "male"),
        "grandfather2": _member("GRANDFATHER2", "male"),
    }


_FETUS_PARENTS = [_parent_child("FATHER", "FETUS", "father"), _parent_child("PLASMA1", "FETUS", "mother")]


def test_two_samples_of_one_person_may_be_linked_when_nothing_they_record_differs() -> None:
    # Unknown agrees with any value: the second plasma records no status of its own.
    _validate_relationship_graph(
        [*_FETUS_PARENTS, _same("PLASMA1", "PLASMA2"), _parent_child("GRANDFATHER1", "PLASMA1", "father")],
        target_members=_pregnancy(clinical_status="unknown", carrier_status="unknown"),
    )


@pytest.mark.parametrize(
    ("second_plasma", "relationships", "detail"),
    [
        ({}, [_same("PLASMA2", "plasma2")], "not linked to itself as the same individual"),
        ({}, [_same("PLASMA1", "PLASMA2"), _same("PLASMA2", "PLASMA1")], "linked as the same individual twice"),
        ({}, [_parent_child("PLASMA1", "PLASMA2", "mother"), _same("PLASMA1", "PLASMA2")], "also as relatives"),
        ({}, [_relative("PLASMA1", "PLASMA2"), _same("PLASMA1", "PLASMA2")], "also as relatives"),
        (
            {"sex": "male"},
            [_same("PLASMA1", "PLASMA2")],
            "PLASMA1 and PLASMA2 are linked as the same individual but their sex differs (female, male)",
        ),
        ({"clinical_status": "affected"}, [_same("PLASMA1", "PLASMA2")], "clinical status differs (affected, unaffected)"),
        ({"carrier_status": "not_carrier"}, [_same("PLASMA1", "PLASMA2")], "carrier status differs (carrier, not_carrier)"),
        (
            {},
            [
                _same("PLASMA1", "PLASMA2"),
                _parent_child("GRANDFATHER1", "PLASMA1", "father"),
                _parent_child("GRANDFATHER2", "PLASMA2", "father"),
            ],
            "another father is recorded for each",
        ),
        (
            # Through another sample: PLASMA3 links both, so the three are one person.
            {"sex": "male"},
            [_same("PLASMA1", "PLASMA3"), _same("PLASMA3", "PLASMA2")],
            "PLASMA1, PLASMA2 and PLASMA3 are linked as the same individual but their sex differs",
        ),
    ],
)
def test_a_same_individual_link_is_refused_where_the_samples_disagree_on_the_person(
    second_plasma: dict[str, object], relationships: list[dict[str, object]], detail: str
) -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_relationship_graph(relationships, target_members=_pregnancy(**second_plasma))

    assert exc.value.status_code == 400
    assert detail in str(exc.value.detail)


def test_a_structure_update_without_same_individual_links_keeps_them_and_with_them_replaces_them() -> None:
    members = _pregnancy()
    current = [*_FETUS_PARENTS, _same("PLASMA1", "PLASMA2")]

    def rows(relationships: dict[str, object]) -> list[tuple[object, ...]]:
        update = FamilyStructureUpdate.model_validate({"relationships": relationships})
        return sorted(
            (row["relationship_type"], row["sample_id_a"], row["sample_id_b"])
            for row in _relationship_rows_from_payload(update, target_members=members, current_relationships=current)
            if row["relationship_type"] == "same_individual"
        )

    assert rows({}) == [("same_individual", "PLASMA1", "PLASMA2")]
    assert rows({"same_individual": [{"samples": ["PLASMA3", "PLASMA2"]}]}) == [("same_individual", "PLASMA3", "PLASMA2")]
    assert rows({"same_individual": []}) == []


def test_a_same_individual_link_is_one_link_whichever_sample_it_names_first() -> None:
    assert _relationship_key(_same("PLASMA1", "PLASMA2")) == _relationship_key(_same("plasma2", "PLASMA1"))
    payload = _link_payload([*_FETUS_PARENTS, _same("PLASMA1", "PLASMA2"), _same("PLASMA2", "PLASMA1")])

    assert [link.samples for link in payload.same_individual or []] == [["PLASMA1", "PLASMA2"]]
    assert payload.relatives == []


def test_the_samples_of_one_person_are_the_samples_the_links_join() -> None:
    assert same_individual_groups([("a", "b"), ("c", "d"), ("b", "e")]) == [["a", "b", "e"], ["c", "d"]]
    assert same_individual_groups([]) == []


def test_a_new_family_from_a_package_is_refused_a_link_between_samples_that_disagree() -> None:
    members = [
        {"sample_id": "PLASMA1", "sex": "female", "clinical_status": "unknown", "carrier_status": "unknown"},
        {"sample_id": "PLASMA2", "sex": "male", "clinical_status": "unknown", "carrier_status": "unknown"},
    ]
    refuse_package_same_individual_conflicts(members[:1], [_same("PLASMA1", "ELSEWHERE")])

    with pytest.raises(HTTPException) as exc:
        refuse_package_same_individual_conflicts(members, [_same("PLASMA1", "PLASMA2")])

    assert "their sex differs (female, male)" in str(exc.value.detail)


def _from(source: str, link: dict[str, object]) -> dict[str, object]:
    return {**link, "source": source, "metadata": {}}


def test_an_overwrite_naming_the_familys_links_again_takes_each_once() -> None:
    # An overwrite gives the payload the package's links (source "manifest") and the family's
    # links it keeps (as a structure edit stored them, "manual"): one pair is one link whatever
    # its source, so the update is not refused as naming it twice.
    members = _pregnancy()
    package = [_from("manifest", _same("PLASMA2", "PLASMA1")), *(_from("manifest", link) for link in _FETUS_PARENTS)]
    family = [_from("manual", _same("PLASMA1", "PLASMA2")), *(_from("manual", link) for link in _FETUS_PARENTS)]

    payload = _link_payload([*package, *family])

    assert [link.samples for link in payload.same_individual or []] == [["PLASMA2", "PLASMA1"]]
    assert [(link.parent, link.child) for link in payload.parent_child] == [("FATHER", "FETUS"), ("PLASMA1", "FETUS")]
    update = FamilyStructureUpdate(relationships=payload)
    rows = _relationship_rows_from_payload(update, target_members=members, current_relationships=family)
    _validate_relationship_graph(rows, target_members=members)


@pytest.mark.parametrize(
    ("relationships", "detail"),
    [
        (
            # Through PLASMA3, PLASMA1 and PLASMA2 are one person: not also parent and child.
            [_same("PLASMA1", "PLASMA3"), _same("PLASMA3", "PLASMA2"), _parent_child("PLASMA1", "PLASMA2", "mother")],
            "PLASMA1 and PLASMA2 are linked as the same individual and also as relatives",
        ),
        (
            # PLASMA1 is the fetus's mother, and PLASMA2, the same person, the fetus's child.
            [*_FETUS_PARENTS, _same("PLASMA1", "PLASMA2"), _parent_child("FETUS", "PLASMA2", "parent")],
            "Parent-child relationships contain a cycle",
        ),
        (
            # The fetus's two parents are two samples of one person, each of unknown sex.
            [
                _parent_child("PLASMA3", "FETUS", "father"),
                _parent_child("GRANDFATHER3", "FETUS", "mother"),
                _same("PLASMA3", "GRANDFATHER3"),
            ],
            "FETUS's parents PLASMA3 and GRANDFATHER3 are linked as the same individual",
        ),
        (
            # A parent role on one sample, another person's on the other: three parents.
            [
                _parent_child("GRANDFATHER1", "PLASMA1", "father"),
                _parent_child("PLASMA3", "PLASMA1", "parent"),
                _parent_child("GRANDFATHER3", "PLASMA2", "parent"),
                _same("PLASMA1", "PLASMA2"),
            ],
            "more than two parents are recorded for them",
        ),
        (
            # One person recorded as the father of one sample and the mother of the other.
            [
                _parent_child("PLASMA3", "PLASMA1", "father"),
                _parent_child("GRANDFATHER3", "PLASMA2", "mother"),
                _same("PLASMA3", "GRANDFATHER3"),
                _same("PLASMA1", "PLASMA2"),
            ],
            "one person is recorded as their father and their mother",
        ),
    ],
)
def test_a_same_individual_link_is_read_over_the_whole_person(relationships: list[dict[str, object]], detail: str) -> None:
    members = {**_pregnancy(), "grandfather3": _member("GRANDFATHER3", "und")}

    with pytest.raises(HTTPException) as exc:
        _validate_relationship_graph(relationships, target_members=members)

    assert exc.value.status_code == 400
    assert detail in str(exc.value.detail)


def test_two_samples_of_one_child_may_record_two_samples_of_one_mother() -> None:
    members = {**_pregnancy(), "fetus2": _member("FETUS2", "und")}
    _validate_relationship_graph(
        [
            *_FETUS_PARENTS,
            _parent_child("FATHER", "FETUS2", "father"),
            _parent_child("PLASMA2", "FETUS2", "mother"),
            _same("PLASMA1", "PLASMA2"),
            _same("FETUS", "FETUS2"),
        ],
        target_members=members,
    )


def test_a_new_family_from_a_package_is_refused_samples_of_one_person_recorded_as_relatives() -> None:
    members = [
        {"sample_id": sample_id, "sex": "female", "clinical_status": "unknown", "carrier_status": "unknown"}
        for sample_id in ("PLASMA1", "PLASMA2", "PLASMA3")
    ]
    with pytest.raises(HTTPException) as exc:
        refuse_package_same_individual_conflicts(
            members,
            [_same("PLASMA1", "PLASMA3"), _same("PLASMA3", "PLASMA2"), _parent_child("PLASMA1", "PLASMA2", "mother")],
        )

    assert "PLASMA1 and PLASMA2 are linked as the same individual and also as relatives" in str(exc.value.detail)
