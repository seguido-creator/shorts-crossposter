"""Builds Google API clients from refresh tokens stored as GitHub secrets.

Two kinds of token are used, because a YouTube Brand Account channel cannot
read your personal Google Drive:
  * GOOGLE_REFRESH_TOKEN  - your personal Google account (Drive read + Sheets)
  * YT_REFRESH_TOKEN_<CH> - one per YouTube channel (upload + read)
Client ID / secret are shared (YT_CLIENT_ID / YT_CLIENT_SECRET).
"""
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_URI = "https://oauth2.googleapis.com/token"

DATA_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]
YT_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class MissingSecret(RuntimeError):
    pass


def _env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise MissingSecret(f"GitHub secret {name} is not set")
    return val


def _creds(refresh_token_env: str, scopes) -> Credentials:
    return Credentials(
        token=None,
        refresh_token=_env(refresh_token_env),
        token_uri=TOKEN_URI,
        client_id=_env("YT_CLIENT_ID"),
        client_secret=_env("YT_CLIENT_SECRET"),
        scopes=scopes,
    )


def data_clients():
    """Drive + Sheets clients for the personal account."""
    creds = _creds("GOOGLE_REFRESH_TOKEN", DATA_SCOPES)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return drive, sheets


def youtube_client(token_secret: str):
    creds = _creds(token_secret, YT_SCOPES)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)
