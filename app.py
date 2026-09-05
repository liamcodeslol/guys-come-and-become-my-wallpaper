import base64
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

import keyring
import requests
from flask import Flask, redirect, render_template, request, send_file, session

from wallpaper import generate_wallpaper, set_windows_wallpaper


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "friends.db"
CONFIG_PATH = BASE_DIR / "config.json"
WALLPAPER_PATH = BASE_DIR / "liam_wallpaper.png"
LOG_PATH = BASE_DIR / "liam_wallpaper.log"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

log = logging.getLogger("liam-wallpaper")


def load_config():
    if not CONFIG_PATH.exists():
        raise RuntimeError(
            "config.json is missing. Copy config.example.json to config.json first."
        )

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


CONFIG = load_config()

CLIENT_ID = CONFIG["discord_client_id"]
CLIENT_SECRET = CONFIG["discord_client_secret"]
REDIRECT_URI = CONFIG["redirect_uri"]
OWNER_NAME = CONFIG.get("owner_name", "Liam")

WIDTH = int(CONFIG.get("wallpaper_width", 1920))
HEIGHT = int(CONFIG.get("wallpaper_height", 1080))
CHECK_SECONDS = int(CONFIG.get("avatar_check_seconds", 300))

DISCORD_API = "https://discord.com/api/v10"
TOKEN_URL = f"{DISCORD_API}/oauth2/token"
USER_URL = f"{DISCORD_API}/users/@me"

app = Flask(__name__)

SECRET_FILE = BASE_DIR / ".flask_secret"


def get_secret():
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text(encoding="utf-8").strip()

    secret = os.urandom(32).hex()
    SECRET_FILE.write_text(secret, encoding="utf-8")

    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass

    return secret


app.secret_key = get_secret()


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS friends (
                discord_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                avatar_hash TEXT,
                avatar_url TEXT,
                joined_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )


def credential_name(discord_id):
    return f"liam-wallpaper-refresh-{discord_id}"


def save_refresh_token(discord_id, refresh_token):
    keyring.set_password(
        "LiamWallpaper",
        credential_name(discord_id),
        refresh_token,
    )


def get_refresh_token(discord_id):
    return keyring.get_password(
        "LiamWallpaper",
        credential_name(discord_id),
    )


def delete_refresh_token(discord_id):
    try:
        keyring.delete_password(
            "LiamWallpaper",
            credential_name(discord_id),
        )
    except Exception:
        pass


def oauth_basic_auth():
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def exchange_code(code):
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {oauth_basic_auth()}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=20,
    )

    if not response.ok:
        log.error(
            "Discord token exchange failed: %s %s",
            response.status_code,
            response.text,
        )
        raise RuntimeError("Discord rejected the authorization code.")

    return response.json()



def refresh_access_token(discord_id):
    refresh_token = get_refresh_token(discord_id)

    if not refresh_token:
        return None

    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {oauth_basic_auth()}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=20,
    )

    if not response.ok:
        log.warning(
            "Refresh failed for %s: %s %s",
            discord_id,
            response.status_code,
            response.text,
        )
        return None

    data = response.json()

    new_refresh_token = data.get("refresh_token")
    if new_refresh_token:
        save_refresh_token(discord_id, new_refresh_token)

    return data.get("access_token")


def get_discord_user(access_token):
    response = requests.get(
        USER_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
        },
        timeout=20,
    )

    if not response.ok:
        raise RuntimeError("Could not retrieve your Discord profile.")

    return response.json()


def avatar_url(user):
    avatar = user.get("avatar")
    user_id = user["id"]

    if not avatar:
        return (
            f"https://cdn.discordapp.com/embed/avatars/"
            f"{int(user_id) % 5}.png"
        )

    return (
        f"https://cdn.discordapp.com/avatars/"
        f"{user_id}/{avatar}.png?size=512"
    )


def rebuild_wallpaper():
    try:
        with db() as connection:
            friends = connection.execute(
                """
                SELECT discord_id, username, avatar_url
                FROM friends
                ORDER BY joined_at ASC
                """
            ).fetchall()

        friend_data = []

        for friend in friends:
            friend_data.append(
                {
                    "discord_id": friend["discord_id"],
                    "username": friend["username"],
                    "avatar_url": friend["avatar_url"],
                }
            )

        generate_wallpaper(
            friend_data,
            str(WALLPAPER_PATH),
            WIDTH,
            HEIGHT,
        )

        set_windows_wallpaper(str(WALLPAPER_PATH))

        log.info(
            "Wallpaper rebuilt with %d friend(s).",
            len(friend_data),
        )

    except Exception:
        log.exception("Wallpaper rebuild failed.")


def check_friends():
    while True:
        try:
            with db() as connection:
                friends = connection.execute(
                    """
                    SELECT discord_id, avatar_hash
                    FROM friends
                    """
                ).fetchall()

            changed = False

            for friend in friends:
                discord_id = friend["discord_id"]

                try:
                    access_token = refresh_access_token(discord_id)

                    if not access_token:
                        continue

                    user = get_discord_user(access_token)

                    new_hash = user.get("avatar")
                    new_url = avatar_url(user)

                    if (
                        new_hash != friend["avatar_hash"]
                    ):
                        with db() as connection:
                            connection.execute(
                                """
                                UPDATE friends
                                SET username = ?,
                                    avatar_hash = ?,
                                    avatar_url = ?,
                                    updated_at = ?
                                WHERE discord_id = ?
                                """,
                                (
                                    user.get("username", "Discord user"),
                                    new_hash,
                                    new_url,
                                    int(time.time()),
                                    discord_id,
                                ),
                            )

                        changed = True
                        log.info(
                            "Avatar changed for %s.",
                            discord_id,
                        )

                except Exception:
                    log.exception(
                        "Could not update friend %s.",
                        discord_id,
                    )

            if changed:
                rebuild_wallpaper()

        except Exception:
            log.exception("Friend checker crashed.")

        time.sleep(CHECK_SECONDS)


@app.route("/")
def index():
    return render_template(
        "index.html",
        owner=OWNER_NAME,
    )

@app.route("/favicon")
def favicon():
    return send_file("favicon.png", mimetype="image/png")

@app.route("/wallpaper")
def wallpaper_image():
    from flask import send_file

    if not WALLPAPER_PATH.exists():
        rebuild_wallpaper()

    return send_file(
        WALLPAPER_PATH,
        mimetype="image/png",
        max_age=0,
    )

@app.route("/join")
def join():
    state = os.urandom(24).hex()
    session["oauth_state"] = state

    authorize_url = (
        "https://discord.com/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={requests.utils.quote(REDIRECT_URI, safe='')}"
        "&scope=identify"
        f"&state={state}"
    )

    return redirect(authorize_url)


@app.route("/oauth/callback")
def oauth_callback():
    try:
        error = request.args.get("error")

        if error:
            return render_template(
                "error.html",
                message=f"Discord authorization failed: {error}",
            )

        returned_state = request.args.get("state")
        expected_state = session.pop("oauth_state", None)

        if not returned_state or returned_state != expected_state:
            return render_template(
                "error.html",
                message="The authorization session expired. Please try again.",
            ), 400

        code = request.args.get("code")

        if not code:
            return render_template(
                "error.html",
                message="Discord did not return an authorization code.",
            ), 400

        token_data = exchange_code(code)
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")

        if not access_token:
            raise RuntimeError("Discord did not return an access token.")

        user = get_discord_user(access_token)

        discord_id = user["id"]
        username = user.get("username", "Discord user")
        avatar_hash = user.get("avatar")
        user_avatar_url = avatar_url(user)

        if refresh_token:
            save_refresh_token(discord_id, refresh_token)

        now = int(time.time())

        with db() as connection:
            connection.execute(
                """
                INSERT INTO friends (
                    discord_id,
                    username,
                    avatar_hash,
                    avatar_url,
                    joined_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(discord_id)
                DO UPDATE SET
                    username = excluded.username,
                    avatar_hash = excluded.avatar_hash,
                    avatar_url = excluded.avatar_url,
                    updated_at = excluded.updated_at
                """,
                (
                    discord_id,
                    username,
                    avatar_hash,
                    user_avatar_url,
                    now,
                    now,
                ),
            )

        rebuild_wallpaper()

        return render_template(
            "success.html",
            owner=OWNER_NAME,
        )

    except Exception as exc:
        log.exception("OAuth callback failed.")

        return render_template(
            "error.html",
            message=str(exc),
        ), 500


if __name__ == "__main__":
    init_db()

    rebuild_wallpaper()

    worker = threading.Thread(
        target=check_friends,
        daemon=True,
    )
    worker.start()

    log.info("Liam Wallpaper started.")

    app.run(
        host="0.0.0.0",
        port=5035,
        debug=False,
    )