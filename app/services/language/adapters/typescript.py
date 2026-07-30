from app.services.language.adapters.javascript import JavaScriptLanguageAdapter


class TypeScriptLanguageAdapter(JavaScriptLanguageAdapter):
    language = "typescript"
    supported_extensions = frozenset({".ts", ".tsx", ".mts", ".cts"})
    project_markers: tuple[str, ...] = (
        "package.json",
        "tsconfig.json",
        "jsconfig.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    )
    base_hints: tuple[str, ...] = (
        "Use tsconfig.json when reasoning about TypeScript compiler behavior.",
        "Prefer preserving existing type strictness and module resolution settings.",
        "Use package.json scripts when suggesting project commands.",
    )
