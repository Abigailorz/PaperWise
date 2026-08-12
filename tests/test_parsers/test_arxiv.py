"""arXiv ID 解析测试"""

from paperwise.parsers.arxiv import extract_arxiv_id, is_arxiv_id


def test_extract_arxiv_id_variants():
    cases = {
        "2401.12345": "2401.12345",
        "2401.12345v3": "2401.12345v3",
        "https://arxiv.org/abs/2401.12345": "2401.12345",
        "https://arxiv.org/pdf/2401.12345v2": "2401.12345v2",
        "http://arxiv.org/abs/1706.03762": "1706.03762",
        "  2310.12345  ": "2310.12345",
    }
    for raw, expected in cases.items():
        assert extract_arxiv_id(raw) == expected, raw


def test_extract_arxiv_id_rejects_garbage():
    for bad in ("", "not an arxiv id", "https://example.com/2401.12345",
                "2401.12"):  # 位数不足
        assert extract_arxiv_id(bad) is None, bad


def test_is_arxiv_id():
    assert is_arxiv_id("https://arxiv.org/abs/2401.12345")
    assert not is_arxiv_id("https://github.com/foo/bar")
