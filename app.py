import os
from flask import Flask, redirect, request, session, render_template, jsonify
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

sp_oauth = SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope="user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private"
)

# Store playback state in memory
playback_store = {}

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
        playback_store[user_id] = {
            "playlist_uri": playlist_uri,
            "track_uri": track_uri,
            "progress_ms": progress_ms
        }
        return {"status": "saved"}
    return {"error": "no playback"}, 400

@app.route("/resume", methods=["POST"])
def resume():
    token_info = session.get("token_info")
    sp = spotipy.Spotify(auth=token_info["access_token"])
    user_id = sp.me()["id"]
    data = request.get_json()
    playlist_uri = data.get("playlist_uri")

    entry = playback_store.get(user_id)
    if entry and entry["playlist_uri"] == playlist_uri:
        sp.start_playback(uris=[entry["track_uri"]], position_ms=entry["progress_ms"])
        return {"status": "resumed"}

    # fallback: just play the playlist
    sp.start_playback(context_uri=playlist_uri)
    return {"status": "started"}

if __name__ == "__main__":
    app.run(debug=True)

