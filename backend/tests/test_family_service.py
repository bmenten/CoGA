from backend.app.services.family_service import _small_variant_presence_filters


def test_small_variant_presence_filters_keep_existing_sample_filters() -> None:
    filters = _small_variant_presence_filters(
        "PROBAND",
        ["MOM:het", "DAD:hom"],
    )

    assert filters == [
        "MOM:het",
        "DAD:hom",
        "PROBAND:0/1|1/0|0|1|1|0|1/1|1|1",
    ]


def test_small_variant_presence_filters_do_not_add_default_when_sample_specific() -> None:
    filters = _small_variant_presence_filters(
        "PROBAND",
        ["PROBAND:het:20", "MOM:het"],
    )

    assert filters == ["PROBAND:het:20", "MOM:het"]
