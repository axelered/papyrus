"""Writes the OpenAPI spec of the app (adr/api.md); the spec is generated, never committed

    uv run python -m scripts.openapi openapi.json

Builds the app without starting it: no config, keys or services are needed.
"""

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description='Write the Papyrus OpenAPI spec as JSON')
    parser.add_argument('path', type=Path, help='output file')
    args = parser.parse_args()

    from server.factory import create_app

    spec = create_app().openapi()
    args.path.write_text(json.dumps(spec, indent=2) + '\n', encoding='utf-8')
    print(f'wrote {args.path} ({len(spec["paths"])} paths, version {spec["info"]["version"]})')


if __name__ == '__main__':
    main()
