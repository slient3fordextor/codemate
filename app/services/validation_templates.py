import re
from typing import Any

TEMPLATES: dict[str, dict[str, Any]] = {
    "python-unit": {
        "id": "python-unit",
        "label": "Python unittest",
        "argv": ["python", "-m", "unittest", "-v"],
    },
    "python-pytest": {
        "id": "python-pytest",
        "label": "Python pytest",
        "argv": ["python", "-m", "pytest", "-q"],
    },
    "python-compile": {
        "id": "python-compile",
        "label": "Python compile",
        "argv": ["python", "-m", "compileall", "-q", "."],
    },
    "python-lint": {
        "id": "python-lint",
        "label": "Python Ruff",
        "argv": ["python", "-m", "ruff", "check", "."],
    },
    "python-types": {
        "id": "python-types",
        "label": "Python Mypy",
        "argv": ["python", "-m", "mypy", "."],
    },
}


def review_diff(diff: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    rules = (
        ("high", re.compile(r"\b(eval|exec)\s*\("), "动态执行需要核对输入来源"),
        ("high", re.compile(r"shell\s*=\s*True|os\.system\s*\("), "命令字符串需要核对注入风险"),
        (
            "medium",
            re.compile(r"except\s*(Exception|BaseException)?\s*:"),
            "宽泛异常处理需要保留失败信息",
        ),
        ("medium", re.compile(r"\b(TODO|FIXME)\b"), "未完成标记需要确认交付范围"),
    )
    findings = []
    path = ""
    line_number = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            line_number = int(match.group(1)) if match else 0
        elif line.startswith("+") and not line.startswith("+++"):
            for severity, pattern, title in rules:
                if pattern.search(line[1:]):
                    findings.append(
                        {
                            "severity": severity,
                            "path": path,
                            "line": line_number,
                            "title": title,
                            "evidence": line[1:],
                        }
                    )
            line_number += 1
        elif line.startswith(" "):
            line_number += 1
    commands = [node for node in nodes if node["type"] == "command"]
    return {
        "kind": "rule-based-review",
        "findings": findings,
        "validation": [
            {
                "node_id": node["id"],
                "state": node["state"],
                "metadata": node["output"].get("metadata", {}),
                "error": node.get("error"),
            }
            for node in commands
        ],
        "requires_manual_review": True,
    }
