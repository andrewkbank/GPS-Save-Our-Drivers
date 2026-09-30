"""
Google Drive Sync Helper for GPS Save Our Drivers.
Provides User OAuth 2.0 desktop authentication and direct sync/download of raw FIT/GPX files
and driver notes to/from the designated team Google Drive folder.
"""

import io
import os
import json
import glob
import time
from typing import Dict, Any, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPES = [
    "https://www.googleapis.com/auth/drive"
]


class DriveSyncHelper:
    """Manages User OAuth authentication and file syncing to/from Google Drive."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.config = self._load_config()
        self._cached_status: Optional[Dict[str, Any]] = None
        self._status_cache_time: float = 0.0
        self._cached_folder_files: Optional[List[Dict[str, Any]]] = None
        self._files_cache_time: float = 0.0

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

    def get_status(self, force_refresh: bool = False, fast: bool = False) -> Dict[str, Any]:
        """Check Drive configuration, credentials, and connectivity (cached with 60s TTL)."""
        now = time.time()
        if not force_refresh and self._cached_status is not None and (now - self._status_cache_time < 60.0):
            return self._cached_status

        has_secret = bool(os.path.exists(self.client_secret_path))
        has_folder = bool(self.folder_id)
        creds = self.get_credentials()
        is_authenticated = bool(creds is not None)

        folder_name = None
        if not fast and is_authenticated and has_folder:
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

        status_result = {
            "enabled": self.config.get("enabled", True),
            "configured": has_secret and has_folder,
            "authenticated": is_authenticated,
            "folder_id": self.folder_id or "(Not specified)",
            "folder_name": folder_name,
            "client_secret_found": has_secret,
            "message": message
        }
        self._cached_status = status_result
        self._status_cache_time = now
        return status_result

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

            self._cached_status = None
            self._cached_folder_files = None

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
        """
        Upload specified files to Google Drive folder, updating existing files in-place
        or uploading new files if not present in the Drive folder.
        """
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

            # Query existing files in destination folder (with pagination)
            existing_items = self.list_folder_files()
            # Group existing items by normalized lowercase name
            existing_files_lower: Dict[str, List[Dict[str, Any]]] = {}
            for item in existing_items:
                name = item.get("name", "")
                if name and "id" in item:
                    existing_files_lower.setdefault(name.lower(), []).append(item)

            synced = []
            for fp in filepaths:
                if not fp or not os.path.exists(fp):
                    continue

                target_fname = os.path.basename(fp)
                ext = os.path.splitext(target_fname)[1].lower()
                target_lower = target_fname.lower()

                # Rule 1: GPS files (.fit, .gpx) are immutable.
                # If already present on Google Drive, skip uploading to save bandwidth and avoid duplicates.
                if ext in (".fit", ".gpx"):
                    if target_lower in existing_files_lower:
                        matches = existing_files_lower[target_lower]
                        matched_id = matches[0]["id"]

                        # Clean up any redundant duplicate GPS files in Drive from previous runs
                        if len(matches) > 1:
                            for duplicate in matches[1:]:
                                try:
                                    service.files().delete(fileId=duplicate["id"], supportsAllDrives=True).execute()
                                except Exception as del_err:
                                    print(f"[DriveSync] Note: could not delete duplicate {duplicate['id']}: {del_err}")

                        synced.append({
                            "name": target_fname,
                            "id": matched_id,
                            "action": "skipped_already_exists"
                        })
                        continue

                # Rule 2: Notes (.json, .txt, .log) should always be overwritten/updated in-place.
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

                if target_lower in existing_files_lower:
                    # Update existing file content in-place
                    matches = existing_files_lower[target_lower]
                    file_id = matches[0]["id"]
                    service.files().update(
                        fileId=file_id,
                        media_body=media,
                        supportsAllDrives=True
                    ).execute()
                    synced.append({"name": target_fname, "id": file_id, "action": "updated"})

                    # If multiple copies existed with the same name, delete redundant duplicates
                    if len(matches) > 1:
                        for duplicate in matches[1:]:
                            try:
                                service.files().delete(fileId=duplicate["id"], supportsAllDrives=True).execute()
                            except Exception as del_err:
                                print(f"[DriveSync] Note: could not delete duplicate {duplicate['id']}: {del_err}")
                else:
                    # Create new file inside target folder
                    meta = {
                        "name": target_fname,
                        "parents": [self.folder_id]
                    }
                    created = service.files().create(
                        body=meta,
                        media_body=media,
                        fields="id, name",
                        supportsAllDrives=True
                    ).execute()
                    file_id = created.get("id")
                    existing_files_lower.setdefault(target_lower, []).append({"name": target_fname, "id": file_id})
                    synced.append({"name": target_fname, "id": file_id, "action": "uploaded"})

            uploaded_count = len([s for s in synced if s["action"] == "uploaded"])
            updated_count = len([s for s in synced if s["action"] == "updated"])
            skipped_count = len([s for s in synced if s["action"] == "skipped_already_exists"])
            active_synced_count = uploaded_count + updated_count

            self._cached_folder_files = None
            return {
                "success": True,
                "synced_count": active_synced_count,
                "uploaded_count": uploaded_count,
                "updated_count": updated_count,
                "skipped_count": skipped_count,
                "synced_files": synced,
                "folder_id": self.folder_id,
                "message": f"Sync completed: {uploaded_count} uploaded, {updated_count} updated, {skipped_count} unchanged GPS files skipped."
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Google Drive sync failed: {e}"
            }

    def list_folder_files(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """List all files currently residing in the target Google Drive folder using pagination (cached with 60s TTL)."""
        now = time.time()
        if not force_refresh and self._cached_folder_files is not None and (now - self._files_cache_time < 60.0):
            return self._cached_folder_files

        creds = self.get_credentials()
        if not creds or not self.folder_id:
            return []

        try:
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            q_query = f"'{self.folder_id}' in parents and trashed = false"
            files: List[Dict[str, Any]] = []
            page_token = None

            while True:
                res = service.files().list(
                    q=q_query,
                    fields="nextPageToken, files(id, name, size, modifiedTime, mimeType)",
                    pageSize=100,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True
                ).execute()
                files.extend(res.get("files", []))
                page_token = res.get("nextPageToken")
                if not page_token:
                    break

            self._cached_folder_files = files
            self._files_cache_time = now
            return files
        except Exception as e:
            print(f"[DriveSync] Error listing folder files: {e}")
            return []

    def download_missing_files(self, target_dirs: Dict[str, List[str]]) -> Dict[str, Any]:
        """
        Downloads missing remote files from Google Drive and routes them into specific local
        directories based on extension.
        
        Example target_dirs input:
        {
            "/path/to/DATA_RAW": [".fit", ".gpx"],
            "/path/to/DATA_NOTES": [".json", ".txt", ".log"]
        }
        """
        if not self.folder_id:
            return {
                "success": False,
                "error": "Target folder_id is not specified in config/app_config.json"
            }

        creds = self.get_credentials()
        if not creds:
            return {
                "success": False,
                "error": "Google Drive authentication required."
            }

        try:
            service = build("drive", "v3", credentials=creds, cache_discovery=False)

            # Ensure local directories exist and build normalized extension lookups
            ext_map = {}
            for dir_path, ext_list in target_dirs.items():
                os.makedirs(dir_path, exist_ok=True)
                for ext in ext_list:
                    ext_map[ext.lower()] = dir_path

            # Get complete remote files list with pagination
            remote_files = self.list_folder_files()
            downloaded = []

            for remote_file in remote_files:
                file_id = remote_file.get("id")
                file_name = remote_file.get("name", "")
                
                # Skip sub-folders / native Google Workspace Docs
                if remote_file.get("mimeType") == "application/vnd.google-apps.folder":
                    continue

                ext = os.path.splitext(file_name)[1].lower()

                # Determine correct local directory target for this file extension
                dest_dir = ext_map.get(ext)
                if not dest_dir:
                    continue

                local_file_path = os.path.join(dest_dir, file_name)

                # Skip download if file already exists locally (case-insensitive check)
                existing_local_names = {f.lower() for f in os.listdir(dest_dir)}
                if file_name.lower() in existing_local_names:
                    continue

                # For notes JSON files, avoid downloading if either {clean_id}_notes.json
                # or legacy {clean_id}.json already exists locally
                if ext == ".json":
                    if file_name.endswith("_notes.json"):
                        legacy_name = file_name.replace("_notes.json", ".json")
                        if os.path.exists(os.path.join(dest_dir, legacy_name)):
                            continue
                    else:
                        base_name = os.path.splitext(file_name)[0]
                        standard_name = f"{base_name}_notes.json"
                        if os.path.exists(os.path.join(dest_dir, standard_name)):
                            continue

                # Stream and save missing file
                request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
                with open(local_file_path, "wb") as f:
                    downloader = MediaIoBaseDownload(f, request)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()

                downloaded.append({
                    "name": file_name, 
                    "id": file_id, 
                    "destination": dest_dir
                })

            self._cached_folder_files = None
            return {
                "success": True,
                "downloaded_count": len(downloaded),
                "downloaded_files": downloaded,
                "message": f"Successfully downloaded {len(downloaded)} missing file(s) to target folders."
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Downloading files failed: {e}"
            }