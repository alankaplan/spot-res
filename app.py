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

    try:
        with open(filename, "rb") as f:
            content = f.read()
        encoded_content = base64.b64encode(content).decode("utf-8")

        headers = {"Authorization": f"token {token}"}
        response = requests.get(api_url, headers=headers, params={"ref": branch})
        sha = response.json().get("sha") if response.status_code == 200 else None

        data = {
            "message": f"Update playback data {datetime.utcnow().isoformat()}",
            "content": encoded_content,
            "branch": branch,
            "sha": sha if sha else None
        }

        put_response = requests.put(api_url, headers=headers, json=data)
        if put_response.status_code in [200, 201]:
            print("✅ playback_data.json pushed to GitHub.")
        else:
            print("❌ Failed to push JSON to GitHub:", put_response.text)
    except Exception as e:
        print("❌ GitHub Push Error:", str(e))


load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

sp_oauth = SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope="user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private"
)

PLAYBACK_FILE = "playback_data.json"
GITHUB_RAW_JSON_URL = os.getenv("GITHUB_JSON_URL")

try:
    response = requests.get(GITHUB_RAW_JSON_URL)
    response.raise_for_status()
    playback_store = response.json()
except Exception as e:
    print("Failed to load playback data from GitHub:", e)
    playback_store = {}


def save_playback_data():
    with open(PLAYBACK_FILE, "w") as f:
        json.dump(playback_store, f, indent=2)
    push_json_to_github()


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

    saved_data = playback_store.get(user_id, {})
    result = [
        {
            "name": pl["name"],
            "uri": pl["uri"],
            "progress_pct": calculate_progress(sp, pl, saved_data.get(pl["uri"]))
        }
        for pl in playlists
    ]
    return jsonify(result)


def calculate_progress(sp, playlist, saved):
    if not saved:
        return None

    try:
        playlist_id = playlist["id"]
        tracks = sp.playlist_items(playlist_id, fields="items.track.uri,total")["items"]
        track_uris = [item["track"]["uri"] for item in tracks if item["track"]]

        if saved["track_uri"] in track_uris:
            index = track_uris.index(saved["track_uri"])
            return int((index / len(track_uris)) * 100)
    except Exception as e:
        print("Error processing playlist progress:", e)
    return None


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

        playback_store.setdefault(user_id, {})[playlist_uri] = {
            "track_uri": track_uri,
            "progress_ms": progress_ms
        }
        save_playback_data()
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
            position_ms=0
        )
        return {"status": "resumed"}

    sp.start_playback(context_uri=playlist_uri)
    return {"status": "started"}


@app.route("/pause", methods=["POST"])
def pause():
    return control_playback("pause_playback", "paused")


@app.route("/play", methods=["POST"])
def play():
    return control_playback("start_playback", "resumed")


def control_playback(action, success_status):
    token_info = session.get("token_info")
    sp = spotipy.Spotify(auth=token_info["access_token"])
    try:
        getattr(sp, action)()
        return {"status": success_status}
    except spotipy.exceptions.SpotifyException as e:
        return {"error": str(e)}, 400


@app.route("/playback_state")
def playback_state():
    token_info = session.get("token_info")
    sp = spotipy.Spotify(auth=token_info["access_token"])
    playback = sp.current_playback()
    return {"is_playing": playback.get("is_playing", False) if playback else False}


if __name__ == "__main__":
    app.run(debug=True)

