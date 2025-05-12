import os
import json
from flask import Flask, redirect, request, session, render_template, jsonify
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import base64
import requests
from datetime import datetime

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

sp_oauth = SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope="user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private",
    cache_path=".cache"
)

PLAYBACK_FILE = "playback_data.json"
GITHUB_RAW_JSON_URL = os.getenv("GITHUB_JSON_URL")

# Load playback data from GitHub
try:
    response = requests.get(GITHUB_RAW_JSON_URL)
    response.raise_for_status()
    playback_store = response.json()
except Exception as e:
    print("Failed to load playback data from GitHub:", e)
    playback_store = {}

def save_playback_data():
    with open(PLAYBACK_FILE, "w") as f:
        json.dump(playback_store, f)

def get_spotify_client():
    token_info = session.get("token_info")

    if not token_info:
        return None

    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
        session["token_info"] = token_info

    return spotipy.Spotify(auth=token_info["access_token"])

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
    sp = get_spotify_client()
    if not sp:
        return {"error": "User not authenticated"}, 401

    user_id = sp.me()["id"]
    playlists = sp.current_user_playlists()["items"]

    result = []
    saved_data = playback_store.get(user_id, {})

    for playlist in playlists:
        uri = playlist["uri"]
        saved = saved_data.get(uri)
        track_info = "No data saved"
        progress_pct = None

        if saved:
            try:
                track_info = sp.track(saved["track_uri"])
                track_info = f"{track_info['artists'][0]['name']} - {track_info['album']['name']} - {track_info['name']}"
                progress_pct = saved.get("progress_ms", 0) / track_info["duration_ms"] * 100
            except:
                track_info = "Saved track not found"

        else:
            # Show first track info if not saved
            try:
                first_track = sp.playlist_tracks(uri)["items"][0]["track"]
                track_info = f"{first_track['artists'][0]['name']} - {first_track['album']['name']} - {first_track['name']}"
            except:
                track_info = "No tracks available"

        result.append({
            "name": playlist["name"],
            "uri": uri,
            "track_info": track_info,
            "progress_pct": progress_pct
        })

    # Sort playlists alphabetically
    result.sort(key=lambda x: x["name"])

    return jsonify(result)

@app.route("/save")
def save_playback():
    sp = get_spotify_client()
    if not sp:
        return {"error": "User not authenticated"}, 401

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
        try:
            # Attempt to resume playback
            sp.start_playback(
                context_uri=playlist_uri,
                offset={"uri": entry["track_uri"]},
                position_ms=0
            )
            return {"status": "resumed"}
        except spotipy.exceptions.SpotifyException as e:
            if "NO_ACTIVE_DEVICE" in str(e):
                # Auto-activate a device
                devices = sp.devices()["devices"]
                if devices:
                    first_device_id = devices[0]["id"]
                    sp.transfer_playback(first_device_id, force_play=True)
                    # Retry starting playback
                    sp.start_playback(
                        context_uri=playlist_uri,
                        offset={"uri": entry["track_uri"]},
                        position_ms=0
                    )
                    return {"status": "resumed (device activated)"}
                else:
                    return {"error": "No active device found and no devices available"}, 400

    # fallback: just play the playlist
    sp.start_playback(context_uri=playlist_uri)
    return {"status": "started"}


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)

