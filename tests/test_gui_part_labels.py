"""Display references must not replace meaningful names or source identities."""

from copy import deepcopy

import pytest


pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def labels():
    pytest.importorskip("PyQt5.QtWidgets")
    pytest.importorskip("pyvistaqt")
    from layerloom.gui_app import _load_current_gui_module

    return _load_current_gui_module()._V58


@pytest.mark.parametrize("name", [
    "solid_cyan", "solid_yellow", "woven_green_1to1", "woven_purple_1to1",
    "Left wheel — 12 mm", "_alignment_mark_", "object_holder", "Part 007",
    "preview_id_bracket", "tmp_weave_guide", "named__inst_custom",
    "named__inst_001", "named__details", "日本語の部品",
])
def test_meaningful_source_names_stay_exact(labels, name):
    objects = [("12", name)]
    assert labels._build_preferred_display_name_map(objects, dict(objects), {"12": "named"}) == {
        "12": name,
    }


def test_numbers_follow_mesh_order_and_only_generic_labels_change(labels):
    objects = [("10", "object_67"), ("2", "part_001"), ("7", ""), ("1", "Wheel")]
    names = dict(objects, assembly="object_9")
    assert labels._build_part_number_map(objects) == {"10": "1", "2": "2", "7": "3", "1": "4"}
    assert labels._build_preferred_display_name_map(objects, names, {}) == {
        "10": "Part 1", "2": "Part 2", "7": "Part 3", "1": "Wheel", "assembly": "object_9",
    }
    # Duplicate references to an OID still refer to one assignable part.
    assert labels._build_part_number_map([*objects, objects[0]]) == labels._build_part_number_map(objects)


def test_duplicate_meaningful_names_use_separate_numbers(labels):
    objects = [("5", "Wheel"), ("8", "Wheel")]
    assert labels._build_preferred_display_name_map(objects, dict(objects), dict(objects)) == dict(objects)
    assert labels._build_part_number_map(objects) == {"5": "1", "8": "2"}


@pytest.mark.parametrize(("raw", "expected"), [
    ("object_5", "Wheel"),
    ("Wheel__3dmodel_5__inst_001", "Wheel"),
    ("Wheel__member_with_underscores_5__inst_1000", "Wheel"),
    ("Wheel__description", "Wheel__description"),
    ("Wheel__inst_001", "Wheel__inst_001"),
    ("Wheel__3dmodel_not_an_oid__inst_001", "Wheel__3dmodel_not_an_oid__inst_001"),
    ("Wheel__3dmodel_5__inst_1", "Wheel__3dmodel_5__inst_1"),
    ("Different wheel__3dmodel_5__inst_001", "Different wheel__3dmodel_5__inst_001"),
])
def test_import_label_requires_generic_name_or_correlated_normalizer_suffix(labels, raw, expected):
    assert labels._preferred_part_display_name(raw, "Wheel") == expected


def test_numbering_and_display_leave_source_and_export_names_untouched(labels):
    objects = [
        ("1", "object_9__ID_1____PAT_cy__"),
        ("2", "_Wheel___ID_2____PAT_m__"),
        ("3", "Wheel__3dmodel_2__inst_001__ID_3____PAT_y__"),
    ]
    names = dict(objects)
    imported = {"1": "object_9", "2": "Wheel", "3": "Wheel"}
    original = deepcopy((objects, names, imported))
    display = labels._build_preferred_display_name_map(objects, names, imported)
    assert display == {"1": "Part 1", "2": "_Wheel_", "3": "Wheel"}
    assert (objects, names, imported) == original
    # Display numbering cannot leak into the ID/recipe tagging used by exports.
    for oid, raw in objects:
        assert labels._make_labeled_name_with_id(raw, oid, labels._extract_pat(raw)) == raw
