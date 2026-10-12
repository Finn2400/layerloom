from layerloom.tokens import NO_WEAVE_TAG_RE
from layerloom.weave import partition_no_weave_parts


def test_no_weave_tag_is_case_insensitive():
    assert NO_WEAVE_TAG_RE.search("left_eye__NOWEAVE__")
    assert NO_WEAVE_TAG_RE.search("right_eye__noweave__")


def test_partition_preserves_no_weave_label_and_identity():
    body = object()
    left_eye = object()
    right_eye = object()
    parts = [
        ("body__PAT_cmy__", body),
        ("fred_eye_left__PARTID_FRED_EYE_LEFT__NOWEAVE__", left_eye),
        ("fred_eye_right__PARTID_FRED_EYE_RIGHT__NOWEAVE__", right_eye),
    ]

    woven, no_weave = partition_no_weave_parts(parts)

    assert woven == [("body__PAT_cmy__", body)]
    assert no_weave == parts[1:]
    assert no_weave[0][1] is left_eye
    assert no_weave[1][1] is right_eye
