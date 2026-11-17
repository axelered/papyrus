from server.types.markdown import CodeLanguage

# fence tags and magika labels that name a curated language differently
LANGUAGE_ALIASES: dict[str, CodeLanguage] = {
    'py': CodeLanguage.PYTHON,
    'python3': CodeLanguage.PYTHON,
    'js': CodeLanguage.JAVASCRIPT,
    'jsx': CodeLanguage.JAVASCRIPT,
    'mjs': CodeLanguage.JAVASCRIPT,
    'cjs': CodeLanguage.JAVASCRIPT,
    'node': CodeLanguage.JAVASCRIPT,
    'ts': CodeLanguage.TYPESCRIPT,
    'kt': CodeLanguage.KOTLIN,
    'kts': CodeLanguage.KOTLIN,
    'golang': CodeLanguage.GO,
    'rs': CodeLanguage.RUST,
    'h': CodeLanguage.C,
    'c++': CodeLanguage.CPP,
    'cc': CodeLanguage.CPP,
    'cxx': CodeLanguage.CPP,
    'hpp': CodeLanguage.CPP,
    'cs': CodeLanguage.CSHARP,
    'c#': CodeLanguage.CSHARP,
    'rb': CodeLanguage.RUBY,
    'sh': CodeLanguage.BASH,
    'shell': CodeLanguage.BASH,
    'zsh': CodeLanguage.BASH,
    'console': CodeLanguage.BASH,
    'ps1': CodeLanguage.POWERSHELL,
    'pwsh': CodeLanguage.POWERSHELL,
    'postgresql': CodeLanguage.SQL,
    'mysql': CodeLanguage.SQL,
    'yml': CodeLanguage.YAML,
    'jsonc': CodeLanguage.JSON,
    'htm': CodeLanguage.HTML,
    'docker': CodeLanguage.DOCKERFILE,
    'terraform': CodeLanguage.HCL,
    'tf': CodeLanguage.HCL,
}


def resolve_language(tag: str | None) -> CodeLanguage | None:
    """Fence tag or magika label to a curated language; anything else is not AST-chunked"""
    if not tag:
        return None
    key = tag.strip().lower()
    if key in CodeLanguage:
        return CodeLanguage(key)
    return LANGUAGE_ALIASES.get(key)
