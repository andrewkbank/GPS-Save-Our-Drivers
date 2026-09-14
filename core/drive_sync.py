"""
Google Drive Sync Helper for GPS Save Our Drivers.
Provides User OAuth 2.0 desktop authentication and direct sync/download of raw FIT/GPX files
and driver notes to/from the designated team Google Drive folder.
"""

import io
import os
import json
import glob
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
        """
        Upload valid roll files to Google Drive, renaming them using 'roll_id' 
        from their associated JSON metadata file.
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
            # 1. Group input files by stem (filename without extension) to discover pairs
            file_groups = {}
            for fp in filepaths:
                if os.path.exists(fp):
                    stem = os.path.splitext(os.path.basename(fp))[0]
                    file_groups.setdefault(stem, []).append(fp)

            # 2. Filter valid rolls and map local paths to target Drive filenames
            upload_queue = []  # List of tuples: (local_file_path, target_drive_filename)

            for stem, paths in file_groups.items():
                json_path = next((p for p in paths if p.lower().endswith('.json')), None)
                if not json_path:
                    continue  # Skip if no corresponding JSON file exists

                # Read and validate JSON metadata
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    gen_notes = data.get("general_notes")
                    seg_notes = data.get("segment_notes")
                    roll_id = data.get("roll_id")

                    # Check if notes are empty or invalid
                    has_gen_notes = bool(gen_notes and str(gen_notes).strip())
                    has_seg_notes = bool(seg_notes and len(seg_notes) > 0)

                    if not (has_gen_notes or has_seg_notes):
                        continue  # Skip rolls with no notes content

                    # Fallback to stem if roll_id is missing or empty
                    target_base_name = str(roll_id).strip() if roll_id else stem

                except Exception as e:
                    print(f"[DriveSync] Error reading JSON metadata {json_path}: {e}")
                    continue

                # Add all associated files for this valid roll to the upload queue
                for fp in paths:
                    ext = os.path.splitext(fp)[1]
                    drive_filename = f"{target_base_name}{ext}"
                    upload_queue.append((fp, drive_filename))

            # 3. Perform Google Drive upload / update
            service = build("drive", "v3", credentials=creds, cache_discovery=False)

            q_query = f"'{self.folder_id}' in parents and trashed = false"
            res = service.files().list(
                q=q_query,
                fields="files(id, name)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            existing_files = {item["name"]: item["id"] for item in res.get("files", [])}

            synced = []
            for local_path, target_fname in upload_queue:
                ext = os.path.splitext(target_fname)[1].lower()

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

                media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True)

                if target_fname in existing_files:
                    # Update existing file content
                    file_id = existing_files[target_fname]
                    service.files().update(
                        fileId=file_id,
                        media_body=media,
                        supportsAllDrives=True
                    ).execute()
                    synced.append({"name": target_fname, "id": file_id, "action": "updated"})
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
                    synced.append({"name": target_fname, "id": created.get("id"), "action": "uploaded"})

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

    def download_missing_files(self, target_dirs: Dict[str, List[str]]) -> Dict[str, Any]:
        """
        Downloads missing remote files and routes them into specific local directories based on extension.
        
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

            # Get remote files list
            remote_files = self.list_folder_files()
            downloaded = []

            for remote_file in remote_files:
                file_id = remote_file["id"]
                file_name = remote_file["name"]
                
                # Skip sub-folders / native Google Workspace Docs
                if remote_file.get("mimeType") == "application/vnd.google-apps.folder":
                    continue

                ext = os.path.splitext(file_name)[1].lower()

                # Determine correct local directory target for this file extension
                dest_dir = ext_map.get(ext)
                if not dest_dir:
                    # Extension not mapped to any directory, skip downloading
                    continue

                local_file_path = os.path.join(dest_dir, file_name)

                # Skip download if file already exists locally
                if os.path.exists(local_file_path):
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