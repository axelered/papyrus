# 📄 Papyrus

Axelered Papyrus is your single point stop for Agentic document processing.

## 🧑🏼‍💻 Development

The server refuses to start without an API key. For local development create a gitignored `.env`:

```shell
PAPYRUS_AUTH_DISABLED=true
PAPYRUS_CONFIG=config.local.yml
```

```shell
uv run fastapi dev --entrypoint server:app --reload
```

Or run development environment with Dekit

```shell
dekit reload --yes
dekit up
```

Format and Lint

```shell
ruff format
ruff check --fix
```

Test

```shell
uv run pytest
```

## 🐳 Docker

```shell
docker build -t papyrus .
docker run -p 8000:8000 -e PAPYRUS_API_KEY=<key> -v ./config.yml:/etc/papyrus/config.yml:ro papyrus
```

```shell
curl -H "Authorization: Bearer <key>" -F file=@report.docx http://localhost:8000/v1/markdown
```

Configuration reference: `adr/architecture.md`.