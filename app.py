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

sp_oauth = SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope="user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private"
)

# Persistent storage file
PLAYBACK_FILE = "playback_data.json"

# Load playback data from file
GITHUB_RAW_JSON_URL = os.getenv("GITHUB_JSON_URL")

try:
    response = requests.get(GITHUB_RAW_JSON_URL)
    response.raise_for_status()
    playback_store = response.json()
except Exception as e:
    print("Failed to load playback data from GitHub:", e)
    playback_store = {}

# Save playback data to file
def save_playback_data():
    with open(PLAYBACK_FILE, "w") as f:
        json.dump(playback_store, f)

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
    user_id = sp.me()["id"]
    playlists = sp.current_user_playlists()["items"]

    result = []
    saved_data = playback_store.get(user_id, {})

    for playlist in playlists:
        uri = playlist["uri"]
        saved = saved_data.get(uri)
        progress_pct = None

        if saved:
            try:
                playlist_id = playlist["id"]
                tracks = []
                offset = 0

                # Handle paginated results
                while True:
                    response = sp.playlist_items(playlist_id, offset=offset, fields="items.track.uri,total,next")
                    tracks += [item["track"]["uri"] for item in response["items"] if item["track"]]
                    if response.get("next"):
                        offset += len(response["items"])
                    else:
                        break

                if saved["track_uri"] in tracks:
                    index = tracks.index(saved["track_uri"])
                    progress_pct = int((index / len(tracks)) * 100)
            except Exception as e:
                print("Error processing playlist progress:", e)
                progress_pct = None

        result.append({
            "name": playlist["name"],
            "uri": uri,
            "progress_pct": progress_pct
        })

    return jsonify(result)


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

        if user_id not in playback_store:
            playback_store[user_id] = {}

        playback_store[user_id][playlist_uri] = {
            "track_uri": track_uri,
            "progress_ms": progress_ms
        }
        save_playback_data()
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

    entry = playback_store.get(user_id, {}).get(playlist_uri)
    if entry:
        sp.start_playback(
            context_uri=playlist_uri,
            offset={"uri": entry["track_uri"]},
            #position_ms=entry["progress_ms"]
            position_ms=0
        )
        return {"status": "resumed"}

    # fallback: just play the playlist
    sp.start_playback(context_uri=playlist_uri)
    return {"status": "started"}

@app.route("/pause", methods=["POST"])
def pause():
    token_info = session.get("token_info")
    sp = spotipy.Spotify(auth=token_info["access_token"])
    try:
        sp.pause_playback()
        return {"status": "paused"}
    except spotipy.exceptions.SpotifyException as e:
        return {"error": str(e)}, 400

@app.route("/play", methods=["POST"])
def play():
    token_info = session.get("token_info")
    sp = spotipy.Spotify(auth=token_info["access_token"])
    try:
        sp.start_playback()
        return {"status": "resumed"}
    except spotipy.exceptions.SpotifyException as e:
        return {"error": str(e)}, 400

@app.route("/playback_state")
def playback_state():
    token_info = session.get("token_info")
    sp = spotipy.Spotify(auth=token_info["access_token"])
    playback = sp.current_playback()
    return {"is_playing": playback["is_playing"] if playback else False}


if __name__ == "__main__":
    app.run(debug=True)

