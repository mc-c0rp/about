import requests
import json
from datetime import datetime
import os
import zipfile

settings_file = 'settings.json'
log = []
error_codes = {
    -1: "an error occurred during the update process.",
    -2: "the target file was not found in the release assets.",
    0: "no updates found.",
}

def _log(message: str):
    global log
    log.append(f"[updater.py] - [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]: {message}")
    print(f"[updater.py]: {message}")


# check and download archive from github
def check_and_download() -> int:
    try:
        with open(settings_file, 'r', encoding='utf-8') as f:
            settings = json.load(f)
    except FileNotFoundError:
        _log("settings.json not found! exit...")
        return

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
        _log("unpacking main.zip...")
        with zipfile.ZipFile('main.zip', 'r') as zip_ref:
            zip_ref.extractall('.')
        _log("unpacking complete!\ndeleting main.zip...")
        os.remove('main.zip')
        _log("deleted!\nupdated successfully!")

    _log("log available in update_log.txt\nexit.")
    with open('update_log.txt', 'w', encoding='utf-8') as log_file:
        log_file.write('\n'.join(log))
    # open the main.py
    os.startfile('main.py')

if __name__ == "__main__":
    update()
