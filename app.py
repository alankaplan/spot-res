import os
import json
from flask import Flask, redirect, request, session, render_template, jsonify
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import base64
import requests
from datetime import datetime

def push_json_to_github():
    token = os.getenv("GITHUB_TOKEN")
    owner = os.getenv("GITHUB_REPO_OWNER")
    repo = os.getenv("GITHUB_REPO_NAME")
    branch = os.getenv("GITHUB_BRANCH", "main")
    filename = "playback_data.json"
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{filename}"

    # Read the local JSON file
    with open(filename, "rb") as f:
        content = f.read()
        encoded_content = base64.b64encode(content).decode("utf-8")

    # Check if the file already exists to get the SHA
    headers = {"Authorization": f"token {token}"}
    response = requests.get(api_url, headers=headers, params={"ref": branch})
    if response.status_code == 200:
        sha = response.json()["sha"]
    else:
        sha = None

    data = {
        "message": f"Update playback data {datetime.utcnow().isoformat()}",
        "content": encoded_content,
        "branch": branch
    }
    if sha:
        data["sha"] = sha

    put_response = requests.put(api_url, headers=headers, json=data)

    if put_response.status_code in [200, 201]:
        print("✅ playback_data.json pushed to GitHub.")
    else:
        print("❌ Failed to push JSON to GitHub:", put_response.text)


load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Initialize Spotify OAuth
sp_oauth = SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope="user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private"
)

# File path to store the playback data
PLAYBACK_DATA_FILE = 'playback_data.json'

# Load the playback data from the JSON file
def load_playback_data():
    if os.path.exists(PLAYBACK_DATA_FILE):
        with open(PLAYBACK_DATA_FILE, 'r') as file:
            return json.load(file)
    return {}

# Save the playback data to the JSON file
def save_playback_data(data):
    with open(PLAYBACK_DATA_FILE, 'w') as file:
        json.dump(data, file)

@app.route("/")
def index():
    if "token_info" not in session:
        return redirect("/login")
    return render_template("index.html")

@app.route("/login")
def login():
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    token_info = sp_oauth.get_access_token(code)
    session["token_info"] = token_info
    return redirect("/")

@app.route("/playlists")
def get_playlists():
    token_info = session.get("token_info")
    sp = spotipy.Spotify(auth=token_info["access_token"])
    playlists = sp.current_user_playlists()
    return jsonify(playlists)

@app.route("/save")
def save_playback():
    token_info = session.get("token_info")
    sp = spotipy.Spotify(auth=token_info["access_token"])
    playback = sp.current_playback()
    if playback and playback.get("context"):
        user_id = sp.me()["id"]
        playlist_uri = playback["context"]["uri"]
        track_uri = playback["item"]["uri"]
        progress_ms = playback["progress_ms"]
        
        # Load existing playback data
        playback_data = load_playback_data()

        # Save playback data to the JSON file
        playback_data[user_id] = {
            "playlist_uri": playlist_uri,
            "track_uri": track_uri,
            "progress_ms": progress_ms
        }
        save_playback_data(playback_data)
        push_json_to_github()

        return {"status": "saved"}
    return {"error": "no playback"}, 400

@app.route("/resume", methods=["POST"])
def resume():
    token_info = session.get("token_info")
    sp = spotipy.Spotify(auth=token_info["access_token"])
    user_id = sp.me()["id"]
    data = request.get_json()
    playlist_uri = data.get("playlist_uri")

    playback_data = load_playback_data()
    entry = playback_data.get(user_id)
    if entry and entry["playlist_uri"] == playlist_uri:
        sp.start_playback(
            context_uri=playlist_uri,
            offset={"uri": entry["track_uri"]},
            position_ms=entry["progress_ms"]
        )
        return {"status": "resumed"}

    # fallback: just play the playlist
    sp.start_playback(context_uri=playlist_uri)
    return {"status": "started"}


if __name__ == "__main__":
    app.run(debug=True)

