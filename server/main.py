from server.factory import create_app

# ASGI entrypoint `server.main:app`; kept out of `server/__init__.py` so pool workers do not build the app
app = create_app()
