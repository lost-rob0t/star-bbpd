from __future__ import annotations

from pathlib import Path

PRODUCTION = Path(__file__).parents[1] / "bbp"


def sources():
    return {
        path.relative_to(PRODUCTION).as_posix(): path.read_text(encoding="utf-8")
        for path in PRODUCTION.rglob("*.py")
    }


def test_no_md5_is_used_for_production_identity():
    for path, text in sources().items():
        assert "hashlib.md5" not in text, path
        assert "from hashlib import md5" not in text, path


def test_no_random_relation_identity_path_remains():
    boundary = sources()["document_boundary.py"]
    assert "Relation.set_id" not in boundary
    assert "document_id=str(ulid.new())" not in boundary


def test_no_deprecated_subdomain_predicate_is_emitted():
    subfinder = sources()["tools/subfinder.py"]
    assert '"subdomain"' not in subfinder
    assert "'subdomain'" not in subfinder
