import hmac
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server.config import Settings
from server.errors import Unauthorized

_bearer = HTTPBearer(auto_error=False, description='Static API key from PAPYRUS_API_KEY')


def is_valid_key(candidate: str, keys: tuple[str, ...]) -> bool:
    # compare against every key so timing does not reveal which one matched
    matches = [hmac.compare_digest(candidate.encode(), key.encode()) for key in keys]
    return any(matches)


async def require_api_key(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> None:
    settings: Settings = request.app.state.settings
    if settings.auth_disabled:
        return
    if credentials is None or not is_valid_key(credentials.credentials, settings.api_keys):
        raise Unauthorized('missing or invalid API key')
