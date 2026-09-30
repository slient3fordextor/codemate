"""Check repository-local Markdown link destinations without network requests."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

LINK = re.compile(r"!?\[[^\n]*?\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)")
REFERENCE = re.compile(r"^\s{0,3}\[[^]]+\]:\s*(?:<([^>]+)>|([^\s]+))")
INLINE_CODE = re.compile(r"(`+).*?\1")


def broken_links(root: Path) -> list[str]:
    root = root.resolve()
    documents = sorted(root.glob("README*.md")) + sorted((root / "docs").rglob("*.md"))
    failures: list[str] = []
    for document in documents:
        fence: str | None = None
        for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            marker = re.match(r"(`{3,}|~{3,})", stripped)
            if marker:
                if fence is None:
                    fence = marker[0]
                elif marker[0][0] == fence[0] and len(marker[0]) >= len(fence):
                    fence = None
                continue
            if fence is not None:
                continue
            line = INLINE_CODE.sub("", line)
            matches = list(LINK.finditer(line))
            reference = REFERENCE.match(line)
            if reference:
                matches.append(reference)
            for match in matches:
                destination = match[1] or match[2]
                parsed = urlsplit(destination)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                path = unquote(parsed.path)
                target = root / path.lstrip("/") if path.startswith("/") else document.parent / path
                try:
                    target.resolve().relative_to(root)
                except ValueError:
                    failures.append(
                        f"{document.relative_to(root)}:{number}: escaped: {destination}"
                    )
                    continue
                if not target.exists():
                    failures.append(
                        f"{document.relative_to(root)}:{number}: missing: {destination}"
                    )
    return failures


def main() -> int:
    failures = broken_links(Path(__file__).resolve().parents[1])
    if failures:
        print("\n".join(failures))
        return 1
    print(
        "Repository-local documentation links passed (files only; external URLs/anchors excluded)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
