import os

# stamped by svaraborut/semver in CI from the git tag, never committed (adr/development.md)
__version__ = '0.0.0'

# baked into the runtime image as a build arg, unset in local development
__watermark__ = os.environ.get('WATERMARK') or None
