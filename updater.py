import requests
import json
from datetime import datetime
import os
import zipfile
import subprocess
import sys

settings_file = 'settings.json'
log = []
error_codes = {
    -1: "an error occurred during the update process",
    -2: "the target file was not found in the release assets",
    0: "no updates found",
}

def _log(message: str):
    global log
    for msg in message.split('\n'):
        log.append(f"[updater.py] - [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]: {msg}")
        print(f"[updater.py]: {msg}")

def load_not_update_list(filename=".not-update"):
    if not os.path.exists(filename):
        return []

    ignore = []
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip().replace("\\", "/")
            if line and not line.startswith("#"):
                ignore.append(line)
    return ignore

def should_ignore(path, ignore_list):
    path = path.replace("\\", "/")
    for item in ignore_list:
        if item.endswith("/") and path.startswith(item):
            return True
        if path == item:
            return True
    return False

def install_requirements(req_file="requirements.txt") -> int:
    """
    Устанавливает зависимости из requirements.txt.
    Возвращает 0 если ок, иначе -1.
    """
    if not os.path.exists(req_file):
        _log(f"{req_file} not found, skipping pip install.")
        return 0

    _log(f"installing dependencies from {req_file}...")
    try:
        cmd = [sys.executable, "-m", "pip", "install", "-r", req_file]

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace"
        )

        if proc.stdout:
            _log(proc.stdout.strip())

        if proc.returncode != 0:
            _log(f"pip install failed with code {proc.returncode}")
            return -1

        _log("dependencies installed successfully.")
        return 0

    except Exception as e:
        _log(f"pip install error: {e}")
        return -1

def check_and_download() -> int:
    try:
        with open(settings_file, 'r', encoding='utf-8') as f:
            settings = json.load(f)
    except FileNotFoundError:
        _log("settings.json not found! exit...")
        return -1

    owner = settings.get("repo_owner")
    repo = settings.get("repo_name")
    current_ver = settings.get("current_ver")
    target_filename = "main.zip"

    _log(f"current version is {current_ver}\nchecking for updates...")

    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        latest_release = response.json()
        
        latest_tag = latest_release.get('tag_name')

        if latest_tag == current_ver:
            _log("no updates found.")
            return 0

        _log(f"new version found! {latest_tag}, preparing to update...")

        download_url = None
        for asset in latest_release.get('assets', []):
            if asset['name'] == target_filename:
                download_url = asset['browser_download_url']
                break

        if not download_url:
            _log(f"{target_filename} not found in release assets!")
            return -2

        _log(f"downloading {target_filename} from {download_url}...")
        file_data = requests.get(download_url)
        file_data.raise_for_status()

        with open(target_filename, 'wb') as f:
            f.write(file_data.content)

        _log(f"download complete! ({target_filename})")
        return 1

    except Exception as e:
        _log(f"an error occurred: {e}.")
        return -1

def update():
    result = check_and_download()

    if result != 1:
        _log(f"update failed. code: {result} ({error_codes.get(result, 'unknown error')})")
    else:
        _log("unpacking main.zip with ignore list...")
        ignore_list = load_not_update_list()

        with zipfile.ZipFile('main.zip', 'r') as zip_ref:
            for member in zip_ref.infolist():
                member_path = member.filename.replace("\\", "/")

                if should_ignore(member_path, ignore_list):
                    _log(f"skipped: {member_path}")
                    continue

                zip_ref.extract(member, '.')

        _log("unpacking complete!\ndeleting main.zip...")
        os.remove('main.zip')
        _log("deleted!")

        pip_result = install_requirements("requirements.txt")
        if pip_result != 0:
            _log("requirements install failed. continuing anyway...")

        _log("updated successfully!")

    _log("log available in update_log.txt\nexit from updater.py\nstarting main.py...")
    with open('update_log.txt', 'w', encoding='utf-8') as log_file:
        log_file.write('\n'.join(log))

    if result >= 0:
        os.startfile('main.py')

if __name__ == "__main__":
    update()
