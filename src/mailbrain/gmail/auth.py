"""Gmail OAuth: load/refresh credentials and build the API service.

Scope is gmail.modify (read + label + archive). The tool never deletes,
so no mail.google.com / delete scope is requested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]
from googleapiclient.discovery import build  # type: ignore[import-untyped]

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def load_credentials(credentials_path: Path, token_path: Path) -> Credentials:
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)  # type: ignore[no-untyped-call]

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())  # type: ignore[no-untyped-call]
            token_path.write_text(creds.to_json())  # type: ignore[no-untyped-call]
            return creds
        except Exception:  # noqa: BLE001 - any refresh failure -> re-auth from scratch
            creds = None

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json())
    return creds


def build_service(creds: Credentials) -> Any:
    return build("gmail", "v1", credentials=creds)
