import re
from pathlib import Path

import pytest

from app.openapi import ERROR_CODES

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DOC_NAMES = [
    "architecture.md",
    "api.md",
    "development.md",
    "testing.md",
    "security.md",
    "deployment.md",
    "release-audit.md",
]
ALL_DOCS = [ROOT / "README.md", *(DOCS / name for name in DOC_NAMES)]
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def all_docs_text() -> str:
    return "\n".join(read(path) for path in ALL_DOCS)


@pytest.mark.parametrize("path", ALL_DOCS, ids=lambda p: p.name)
def test_document_exists_and_is_not_a_stub(path):
    assert path.is_file()
    assert len(read(path).split()) > 150


@pytest.mark.parametrize("path", ALL_DOCS, ids=lambda p: p.name)
def test_relative_links_resolve(path):
    for target in LINK.findall(read(path)):
        if target.startswith(("http://", "https://", "mailto:", "#", "/")):
            continue
        assert (path.parent / target.split("#")[0]).exists(), f"{path.name}: broken link {target}"


def test_readme_links_to_every_document():
    readme = read(ROOT / "README.md")
    for name in DOC_NAMES:
        assert f"docs/{name}" in readme


def test_every_variable_in_env_example_is_documented():
    names = {
        match.group(1)
        for line in read(ROOT / ".env.example").splitlines()
        if (match := re.match(r"^([A-Z][A-Z0-9_]*)=", line))
    }
    text = read(DOCS / "development.md")
    assert names, ".env.example has no variables"
    assert [n for n in sorted(names) if f"`{n}`" not in text] == []


def test_every_environment_variable_read_by_the_code_is_documented():
    sources = [*(ROOT / "config").glob("*.py"), ROOT / "gunicorn.conf.py", ROOT / "wsgi.py"]
    used = set()
    for source in sources:
        text = read(source)
        used |= set(re.findall(r"environ(?:\.get)?[(\[]\s*[\"']([A-Z][A-Z0-9_]*)[\"']", text))
        used |= set(re.findall(r"_csv\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']", text))
    docs = all_docs_text()
    assert len(used) >= 15
    assert [n for n in sorted(used) if f"`{n}`" not in docs] == []


def test_api_guide_documents_every_endpoint_and_error_code():
    text = read(DOCS / "api.md")
    for endpoint in ("/api/v1/urls", "/{short_code}", "/health", "/health/ready"):
        assert endpoint in text
    assert [c for c in ERROR_CODES if f"`{c}`" not in text] == []


@pytest.mark.parametrize(
    "snippet",
    [
        "pip install -r requirements-dev.txt",
        "flask --app run db upgrade",
        "python run.py",
        "pytest",
        "ruff check .",
        "docker compose up --build",
    ],
)
def test_readme_documents_core_workflows(snippet):
    assert snippet in read(ROOT / "README.md")


def test_security_guide_covers_the_main_controls():
    text = read(DOCS / "security.md").lower()
    for topic in ("content security policy", "rate limit", "blocklist", "secret_key", "csrf"):
        assert topic in text or topic.replace(" ", "_") in text


def test_deployment_guide_covers_operations():
    text = read(DOCS / "deployment.md").lower()
    for topic in ("gunicorn", "postgresql", "migrations", "health", "backup", "https"):
        assert topic in text
    assert "rollback" in text or "roll back" in text
