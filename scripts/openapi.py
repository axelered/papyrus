"""Print the OpenAPI document without starting the app (used by the docs bundle and docs.axelered.com)

    uv run python scripts/openapi.py               # to stdout
    uv run python scripts/openapi.py openapi.json  # to a file

The spec is generated, never committed (adr/api.md). No config, keys or services are needed.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.factory import create_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description='Print the Papyrus OpenAPI document as JSON')
    parser.add_argument('path', type=Path, nargs='?', help='output file, stdout when omitted')
    args = parser.parse_args()

    text = json.dumps(create_app().openapi(), indent=2) + '\n'
    if args.path is None:
        sys.stdout.write(text)
    else:
        args.path.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()
