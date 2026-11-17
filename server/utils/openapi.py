from typing import Any


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolves local $defs references so the schema can be embedded where components are not registered"""
    definitions = schema.pop('$defs', {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if ref := node.get('$ref'):
                return resolve(definitions[ref.rsplit('/', 1)[-1]])
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)
