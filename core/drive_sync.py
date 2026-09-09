"""
Google Drive Sync Helper for GPS Save Our Drivers.
Provides User OAuth 2.0 desktop authentication and direct sync of raw FIT/GPX files
and driver notes to the designated team Google Drive folder.
"""

import os
import json
import glob
from typing import Dict, Any, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/drive"
]


class DriveSyncHelper:
    """Manages User OAuth authentication and file syncing to Google Drive."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f).get("google_drive", {})
        return {}

    def _resolve_path(self, path_str: str) -> str:
        if not path_str:
            return ""
        if os.path.isabs(path_str):
            return path_str
        return os.path.normpath(os.path.join(self.base_dir, path_str))

    @property
    def folder_id(self) -> str:
        return self.config.get("folder_id", "").strip()

    @property
    def client_secret_path(self) -> str:
        # Check configured path first
        configured = self._resolve_path(self.config.get("client_secret_file", "client_secret.json"))
        if os.path.exists(configured):
            return configured

        # Fallback to any client_secret*.json in root
        matches = glob.glob(os.path.join(self.base_dir, "client_secret*.json"))
        if matches:
            return matches[0]

        return configured

    @property
    def token_path(self) -> str:
        return self._resolve_path(self.config.get("token_file", "token.json"))

    def get_credentials(self) -> Optional[Credentials]:
        """Load and refresh user credentials if available."""
        creds = None
        t_path = self.token_path

        if os.path.exists(t_path):
            try:
                creds = Credentials.from_authorized_user_file(t_path, SCOPES)
            except Exception as e:
                print(f"[DriveSync] Error loading {t_path}: {e}")
                creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(t_path, "w", encoding="utf-8") as token_file:
                    token_file.write(creds.to_json())
            except Exception as e:
                print(f"[DriveSync] Error refreshing token: {e}")
                creds = None

        return creds if (creds and creds.valid) else None

    def get_status(self) -> Dict[str, Any]:
        """Check Drive configuration, credentials, and connectivity."""
        has_secret = bool(os.path.exists(self.client_secret_path))
        has_folder = bool(self.folder_id)
        creds = self.get_credentials()
        is_authenticated = bool(creds is not None)

        folder_name = None
        if is_authenticated and has_folder:
            try:
                service = build("drive", "v3", credentials=creds, cache_discovery=False)
                folder_meta = service.files().get(
                    fileId=self.folder_id,
                    fields="name",
                    supportsAllDrives=True
                ).execute()
                folder_name = folder_meta.get("name")
            except Exception as e:
                folder_name = f"(Access check failed: {e})"

        message = "Google Drive connected and ready"
        if not has_folder:
            message = "Folder ID not set in config/app_config.json"
        elif not has_secret:
            message = "OAuth client_secret.json not found in project root"
        elif not is_authenticated:
            message = "OAuth login required (click 'Connect Google Drive')"
        elif folder_name:
            message = f"Connected to Google Drive folder: '{folder_name}'"

        return {
            "enabled": self.config.get("enabled", True),
            "configured": has_secret and has_folder,
            "authenticated": is_authenticated,
            "folder_id": self.folder_id or "(Not specified)",
            "folder_name": folder_name,
            "client_secret_found": has_secret,
            "message": message
        }

    def authenticate(self, port: int = 0) -> Dict[str, Any]:
        """Launch local browser server flow to authenticate user."""
        secret_path = self.client_secret_path
        if not os.path.exists(secret_path):
            return {
                "success": False,
                "error": f"client_secret.json not found at {secret_path}"
            }

        try:
            flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPES)
            auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
            print(f"\n[OAuth URL] Open this link in your browser if it doesn't open automatically:\n{auth_url}\n", flush=True)

            creds = flow.run_local_server(port=port, prompt="consent")

            with open(self.token_path, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())

            return {
                "success": True,
                "message": "Authentication successful! token.json saved."
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def sync_files(self, filepaths: List[str]) -> Dict[str, Any]:
        """Upload raw files, notes, or processed data to the designated Drive folder."""
        if not self.folder_id:
            return {
                "success": False,
                "error": "Target folder_id is not specified in config/app_config.json"
            }

        creds = self.get_credentials()
        if not creds:
            return {
                "success": False,
                "error": "Google Drive authentication required. Run authenticate() or click Connect Drive."
            }

        try:
            service = build("drive", "v3", credentials=creds, cache_discovery=False)

            # Query existing files in destination folder to update in-place if already present
            q_query = f"'{self.folder_id}' in parents and trashed = false"
            res = service.files().list(
                q=q_query,
                fields="files(id, name)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            existing_files = {item["name"]: item["id"] for item in res.get("files", [])}

            synced = []
            for fp in filepaths:
                if not os.path.exists(fp):
                    continue

                fname = os.path.basename(fp)
                ext = os.path.splitext(fname)[1].lower()

                # Infer MIME type
                if ext == ".json":
                    mimetype = "application/json"
                elif ext == ".gpx":
                    mimetype = "application/gpx+xml"
                elif ext == ".fit":
                    mimetype = "application/octet-stream"
                elif ext in (".txt", ".log"):
                    mimetype = "text/plain"
                else:
                    mimetype = "application/octet-stream"

                media = MediaFileUpload(fp, mimetype=mimetype, resumable=True)

                if fname in existing_files:
                    # Update existing file content
                    file_id = existing_files[fname]
                    service.files().update(
                        fileId=file_id,
                        media_body=media,
                        supportsAllDrives=True
                    ).execute()
                    synced.append({"name": fname, "id": file_id, "action": "updated"})
                else:
                    # Create new file inside target folder
                    meta = {
                        "name": fname,
                        "parents": [self.folder_id]
                    }
                    created = service.files().create(
                        body=meta,
                        media_body=media,
                        fields="id, name",
                        supportsAllDrives=True
                    ).execute()
                    synced.append({"name": fname, "id": created.get("id"), "action": "uploaded"})

            return {
                "success": True,
                "synced_count": len(synced),
                "synced_files": synced,
                "folder_id": self.folder_id,
                "message": f"Successfully synced {len(synced)} file(s) to Google Drive folder."
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Google Drive sync failed: {e}"
            }

    def list_folder_files(self) -> List[Dict[str, Any]]:
        """List files currently residing in the target Google Drive folder."""
        creds = self.get_credentials()
        if not creds or not self.folder_id:
            return []

        try:
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            q_query = f"'{self.folder_id}' in parents and trashed = false"
            res = service.files().list(
                q=q_query,
                fields="files(id, name, size, modifiedTime, mimeType)",
                pageSize=100,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            return res.get("files", [])
        except Exception as e:
            print(f"[DriveSync] Error listing folder files: {e}")
            return []

