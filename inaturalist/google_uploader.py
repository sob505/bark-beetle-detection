#!/usr/bin/env python3
"""
iNaturalist Image Downloader with Google Drive Upload
Downloads observations from iNaturalist and uploads images to Google Drive.
Requires: requests, google-auth-oauthlib, google-auth-httplib2, google-api-python-client
"""

import requests
import json
import time
import os
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
import httplib2

class iNaturalistToGoogleDrive:
    """Download iNaturalist images and upload them to Google Drive"""
    
    # iNaturalist API
    INATURALIST_BASE = "https://api.inaturalist.org/v1"
    
    # Google Drive API
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    
    def __init__(self, credentials_file: str = "credentials.json"):
        """
        Initialize the uploader with Google Drive credentials
        
        Args:
            credentials_file: Path to Google OAuth credentials JSON file
        """
        self.credentials_file = credentials_file
        self.service = None
        self.folder_id = None
        self.temp_dir = Path("./temp_images")
        self.temp_dir.mkdir(exist_ok=True)
    
    def authenticate_google_drive(self) -> bool:
        """Authenticate with Google Drive using OAuth"""
        try:
            creds = None
            token_file = "token.pickle"
            
            # Load saved token if it exists
            if os.path.exists(token_file):
                with open(token_file, 'rb') as token:
                    creds = pickle.load(token)
            
            # If no valid credentials, get new ones
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.credentials_file, self.SCOPES)
                    creds = flow.run_local_server(port=0)
                
                # Save token for future use
                with open(token_file, 'wb') as token:
                    pickle.dump(creds, token)
            
            # Build Google Drive service
            from googleapiclient.discovery import build
            self.service = build('drive', 'v3', credentials=creds)
            
            print("✓ Successfully authenticated with Google Drive")
            return True
        except Exception as e:
            print(f"✗ Authentication error: {e}")
            return False
    
    def create_google_drive_folder(self, folder_path: str, parent_folder_id: str = None) -> bool:
        """Create a folder structure in Google Drive"""
        if not self.service:
            print("✗ Not authenticated. Call authenticate_google_drive() first.")
            return False
        
        try:
            # If no parent specified, use root
            if parent_folder_id is None:
                parent_folder_id = "root"
            
            current_folder_id = parent_folder_id
            
            # Create/navigate to each folder in the path
            for folder_name in folder_path.split('/'):
                if not folder_name:
                    continue
                
                # Check if folder exists
                query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false and '{current_folder_id}' in parents"
                results = self.service.files().list(
                    q=query,
                    spaces='drive',
                    fields='files(id, name)',
                    pageSize=10
                ).execute()
                
                files = results.get('files', [])
                
                if files:
                    # Folder exists, use it
                    current_folder_id = files[0]['id']
                else:
                    # Create new folder
                    file_metadata = {
                        'name': folder_name,
                        'mimeType': 'application/vnd.google-apps.folder',
                        'parents': [current_folder_id]
                    }
                    
                    folder = self.service.files().create(
                        body=file_metadata,
                        fields='id'
                    ).execute()
                    
                    current_folder_id = folder.get('id')
            
            self.folder_id = current_folder_id
            print(f"✓ Created/accessed Google Drive folder: {folder_path}")
            return True
        except Exception as e:
            print(f"✗ Failed to create folder: {e}")
            return False
    
    def search_observations(self, taxon_name: str = None, taxon_id: int = None, 
                           place_id: int = None, per_page: int = 50, 
                           quality_grade: str = "research") -> List[Dict]:
        """Search for iNaturalist observations"""
        params = {
            "quality_grade": quality_grade,
            "per_page": per_page,
            "photos": True  # Only get observations with photos
        }
        
        if taxon_id:
            params["taxon_id"] = taxon_id
        elif taxon_name:
            # First get the taxon ID
            taxa = self._search_taxa(taxon_name)
            if taxa:
                params["taxon_id"] = taxa[0]["id"]
            else:
                print(f"✗ Taxon '{taxon_name}' not found")
                return []
        
        if place_id:
            params["place_id"] = place_id
        
        try:
            response = requests.get(
                f"{self.INATURALIST_BASE}/observations",
                params=params,
                timeout=10
            )
            response.raise_for_status()
            observations = response.json()["results"]
            print(f"✓ Found {len(observations)} observations")
            return observations
        except Exception as e:
            print(f"✗ Error searching observations: {e}")
            return []
    
    def _search_taxa(self, query: str) -> List[Dict]:
        """Search for a taxon by name"""
        try:
            response = requests.get(
                f"{self.INATURALIST_BASE}/taxa",
                params={"q": query},
                timeout=10
            )
            response.raise_for_status()
            return response.json()["results"]
        except Exception as e:
            print(f"✗ Error searching taxa: {e}")
            return []
    
    def download_image(self, photo_url: str, filename: str) -> Optional[Path]:
        """Download an image from iNaturalist"""
        try:
            response = requests.get(photo_url, timeout=10)
            response.raise_for_status()
            
            filepath = self.temp_dir / filename
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            return filepath
        except Exception as e:
            print(f"✗ Failed to download {filename}: {e}")
            return None
    
    def upload_to_google_drive(self, filepath: Path) -> Optional[str]:
        """
        Upload a file to Google Drive
        
        Returns:
            File ID if successful, None otherwise
        """
        if not self.service or not self.folder_id:
            print("  ✗ Not authenticated or folder not set")
            return None
        
        try:
            file_metadata = {
                'name': filepath.name,
                'parents': [self.folder_id]
            }
            
            media = None
            with open(filepath, 'rb') as f:
                from googleapiclient.http import MediaFileUpload
                media = MediaFileUpload(str(filepath), resumable=True)
            
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()
            
            file_id = file.get('id')
            print(f"  ↑ Uploaded {filepath.name} (ID: {file_id})")
            return file_id
        except Exception as e:
            print(f"  ✗ Failed to upload {filepath.name}: {e}")
            return None
    
    def process_observations(self, observations: List[Dict], 
                            google_drive_folder: str = "BeetleDamageImages") -> Dict:
        """
        Download images from observations and upload to Google Drive
        
        Returns:
            Dictionary with processing results and metadata
        """
        if not self.authenticate_google_drive():
            return {"success": False, "error": "Authentication failed"}
        
        if not self.create_google_drive_folder(google_drive_folder):
            return {"success": False, "error": "Failed to create Google Drive folder"}
        
        results = {
            "total": len(observations),
            "successful": 0,
            "failed": 0,
            "images": [],
            "timestamp": datetime.now().isoformat()
        }
        
        for idx, obs in enumerate(observations, 1):
            print(f"\n[{idx}/{len(observations)}] Processing observation {obs['id']}")
            
            # Get first photo
            if not obs.get('photos'):
                print("  ✗ No photos found")
                results["failed"] += 1
                continue
            
            photo = obs['photos'][0]
            photo_url = photo['url']
            
            # Create filename with metadata
            taxon_name = obs.get('taxon', {}).get('name', 'unknown').replace(' ', '_')
            filename = f"{obs['id']}_{taxon_name}.jpg"
            
            # Download
            filepath = self.download_image(photo_url, filename)
            if not filepath:
                results["failed"] += 1
                continue
            
            # Upload
            file_id = self.upload_to_google_drive(filepath)
            if file_id:
                results["successful"] += 1
                results["images"].append({
                    "filename": filename,
                    "file_id": file_id,
                    "inaturalist_id": obs['id'],
                    "taxon": obs.get('taxon', {}).get('name'),
                    "photo_id": photo['id'],
                    "url": photo_url,
                    "uploaded_at": datetime.now().isoformat()
                })
            else:
                results["failed"] += 1
            
            # Clean up temp file
            try:
                filepath.unlink()
            except:
                pass
            
            # Rate limiting
            time.sleep(0.5)
        
        # Clean up temp directory
        try:
            self.temp_dir.rmdir()
        except:
            pass
        
        results["success"] = True
        return results
    
    def save_results(self, results: Dict, filename: str = "upload_results.json"):
        """Save upload results to a file"""
        try:
            with open(filename, 'w') as f:
                json.dump(results, f, indent=2)
            print(f"\n✓ Results saved to {filename}")
        except Exception as e:
            print(f"✗ Failed to save results: {e}")


# ============================================
# SETUP INSTRUCTIONS
# ============================================
"""
To use this script, follow these steps:

1. CREATE A GOOGLE CLOUD PROJECT:
   - Go to https://console.cloud.google.com/
   - Create a new project (name it "iNaturalist")
   - Wait for it to be created

2. ENABLE GOOGLE DRIVE API:
   - In your project, search for "Google Drive API"
   - Click it and click "Enable"

3. CREATE OAUTH CREDENTIALS:
   - Go to "Credentials" (left sidebar)
   - Click "Create Credentials" → "OAuth client ID"
   - If prompted, click "Configure OAuth consent screen" first
     * Choose "External"
     * Fill in basic info (app name: "iNaturalist")
     * Add your email for support contact
     * Add yourself as a test user
     * Save and continue
   - Back to OAuth client ID:
     * Application type: "Desktop application"
     * Name: "iNaturalist"
     * Click Create
   - Click the download icon next to your credentials
   - Save as "credentials.json" in your project folder

4. INSTALL REQUIRED PACKAGES:
   pip install requests google-auth-oauthlib google-auth-httplib2 google-api-python-client

5. RUN THE SCRIPT:
   See the example below. Your browser will open for authentication.
"""


# ============================================
# USAGE EXAMPLE
# ============================================
if __name__ == "__main__":
    print("="*60)
    print("iNaturalist to Google Drive Image Uploader")
    print("="*60)
    
    # Initialize the uploader
    uploader = iNaturalistToGoogleDrive(credentials_file="credentials.json")
    
    # Search for observations (example: bark beetles)
    print("\nStep 1: Searching for observations...")
    observations = uploader.search_observations(
        taxon_name="Scolytinae",  # or use taxon_id=12345
        per_page=10,
        place_id=3,
        quality_grade="research"
    )
    
    if observations:
        # Process observations and upload to Google Drive
        print("\nStep 2: Authenticating with Google Drive and uploading images...")
        results = uploader.process_observations(
            observations,
            google_drive_folder="BeetleDamageDataset"
        )
        
        # Save results
        if results.get('success'):
            uploader.save_results(results)
            
            print(f"\n{'='*60}")
            print(f"Complete! Uploaded: {results['successful']}/{results['total']}")
            print(f"Failed: {results['failed']}")
            print(f"{'='*60}")
            
            # Print Google Drive folder info
            if uploader.folder_id:
                print(f"\nYour images are in this Google Drive folder:")
                print(f"https://drive.google.com/drive/folders/{uploader.folder_id}")
                print(f"\nShare this link with your team!")
        else:
            print(f"\n✗ Upload failed: {results.get('error', 'Unknown error')}")
    else:
        print("No observations found.")
