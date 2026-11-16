from enum import StrEnum


class Tokenizer(StrEnum):
    O200K_BASE = 'o200k_base'
    CL100K_BASE = 'cl100k_base'
    WORD = 'word'
    CHARACTER = 'character'

    @property
    def is_tiktoken(self) -> bool:
        return self in {Tokenizer.O200K_BASE, Tokenizer.CL100K_BASE}


class ChunkKind(StrEnum):
    TEXT = 'text'
    TABLE = 'table'
    CODE = 'code'


class CodeLanguage(StrEnum):
    """Curated tree-sitter grammars baked into the image; values are tree-sitter language names"""

    PYTHON = 'python'
    JAVASCRIPT = 'javascript'
    TYPESCRIPT = 'typescript'
    TSX = 'tsx'
    JAVA = 'java'
    KOTLIN = 'kotlin'
    GO = 'go'
    RUST = 'rust'
    C = 'c'
    CPP = 'cpp'
    CSHARP = 'csharp'
    PHP = 'php'
    RUBY = 'ruby'
    SWIFT = 'swift'
    SCALA = 'scala'
    BASH = 'bash'
    POWERSHELL = 'powershell'
    SQL = 'sql'
    YAML = 'yaml'
    TOML = 'toml'
    JSON = 'json'
    HTML = 'html'
    CSS = 'css'
    DOCKERFILE = 'dockerfile'
    HCL = 'hcl'


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
