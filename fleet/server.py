"""Fleet server — heartbeat ingest, command queue, admin dashboard.

Config via environment:
  FLEET_DB              sqlite path           (default: fleet.db)
  FLEET_ADMIN_PASSWORD  dashboard password    (required; user is 'admin')
  FLEET_ENROLL_KEY      key to register new boxes (required to add boxes)
  FLEET_OFFLINE_AFTER   seconds w/o heartbeat = offline (default: 180)
  FLEET_PORT            listen port           (default: 8080)

Run behind TLS in production (a reverse proxy or `flask`+gunicorn over HTTPS).
"""
from __future__ import annotations

import hmac
import json
import os
import sqlite3
import time
from functools import wraps

from flask import Flask, Response, g, jsonify, redirect, render_template, request, url_for

# Commands the dashboard may queue — must match the agent's ALLOWED_COMMANDS.
ALLOWED_COMMANDS = {"reboot", "update", "restart_gateway", "stop_gateway", "start_gateway"}

DB_PATH = os.environ.get("FLEET_DB", "fleet.db")
ADMIN_PASSWORD = os.environ.get("FLEET_ADMIN_PASSWORD", "")
ENROLL_KEY = os.environ.get("FLEET_ENROLL_KEY", "")
OFFLINE_AFTER = int(os.environ.get("FLEET_OFFLINE_AFTER", "180"))

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #
def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS boxes (
            box_id      TEXT PRIMARY KEY,
            api_key     TEXT NOT NULL,
            name        TEXT,
            last_seen   INTEGER,
            status_json TEXT
        );
        CREATE TABLE IF NOT EXISTS commands (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            box_id   TEXT NOT NULL,
            type     TEXT NOT NULL,
            status   TEXT NOT NULL DEFAULT 'pending',  -- pending|sent|done
            ok       INTEGER,
            output   TEXT,
            created  INTEGER NOT NULL,
            finished INTEGER
        );
        """
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
def _ct_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")


def require_admin(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        auth = request.authorization
        if not ADMIN_PASSWORD or not auth or auth.username != "admin" or not _ct_eq(auth.password, ADMIN_PASSWORD):
            return Response(
                "auth required", 401, {"WWW-Authenticate": 'Basic realm="fleet"'}
            )
        return fn(*a, **kw)

    return wrapper


def _authenticate_box(box_id: str) -> tuple[bool, str]:
    """Verify the bearer key for box_id; enroll new boxes if enroll key matches."""
    bearer = (request.headers.get("Authorization", "")).removeprefix("Bearer ").strip()
    row = db().execute("SELECT api_key FROM boxes WHERE box_id=?", (box_id,)).fetchone()
    if row is None:
        # Unknown box: register only with a valid enroll key.
        if not ENROLL_KEY or not _ct_eq(request.headers.get("X-Enroll-Key", ""), ENROLL_KEY):
            return False, "unknown box and invalid enroll key"
        if not bearer:
            return False, "missing api key"
        db().execute(
            "INSERT INTO boxes (box_id, api_key, last_seen) VALUES (?,?,?)",
            (box_id, bearer, int(time.time())),
        )
        db().commit()
        return True, "enrolled"
    return (_ct_eq(bearer, row["api_key"]), "ok" if _ct_eq(bearer, row["api_key"]) else "bad key")


# --------------------------------------------------------------------------- #
# agent-facing API
# --------------------------------------------------------------------------- #
@app.post("/api/heartbeat")
def heartbeat():
    body = request.get_json(silent=True) or {}
    box_id = body.get("box_id")
    if not box_id:
        return jsonify(error="box_id required"), 400
    ok, msg = _authenticate_box(box_id)
    if not ok:
        return jsonify(error=msg), 401

    name = (body.get("hostname") or box_id)
    db().execute(
        "UPDATE boxes SET name=COALESCE(name, ?), last_seen=?, status_json=? WHERE box_id=?",
        (name, int(time.time()), json.dumps(body), box_id),
    )
    # Hand over pending commands and mark them sent.
    rows = db().execute(
        "SELECT id, type FROM commands WHERE box_id=? AND status='pending' ORDER BY id", (box_id,)
    ).fetchall()
    if rows:
        db().executemany(
            "UPDATE commands SET status='sent' WHERE id=?", [(r["id"],) for r in rows]
        )
    db().commit()
    return jsonify(commands=[{"id": r["id"], "type": r["type"]} for r in rows])


@app.post("/api/command-result")
def command_result():
    body = request.get_json(silent=True) or {}
    box_id = body.get("box_id")
    if not box_id:
        return jsonify(error="box_id required"), 400
    ok, msg = _authenticate_box(box_id)
    if not ok:
        return jsonify(error=msg), 401
    db().execute(
        "UPDATE commands SET status='done', ok=?, output=?, finished=? WHERE id=? AND box_id=?",
        (1 if body.get("ok") else 0, str(body.get("output", ""))[:4000],
         int(time.time()), body.get("command_id"), box_id),
    )
    db().commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# admin dashboard
# --------------------------------------------------------------------------- #
@app.get("/")
@require_admin
def dashboard():
    now = int(time.time())
    rows = db().execute("SELECT * FROM boxes ORDER BY name").fetchall()
    boxes = []
    for r in rows:
        status = json.loads(r["status_json"]) if r["status_json"] else {}
        last = r["last_seen"] or 0
        boxes.append({
            "box_id": r["box_id"],
            "name": r["name"] or r["box_id"],
            "online": (now - last) < OFFLINE_AFTER,
            "last_seen_ago": now - last if last else None,
            "version": status.get("version", "?"),
            "gateway_active": status.get("gateway_active"),
            "cameras_up": status.get("cameras_up"),
            "cameras_total": status.get("cameras_total"),
            "tailscale_ip": status.get("tailscale_ip"),
            "uptime_s": status.get("uptime_s"),
        })
    return render_template("dashboard.html", boxes=boxes, commands=ALLOWED_COMMANDS,
                           offline_after=OFFLINE_AFTER)


@app.post("/boxes/<box_id>/command")
@require_admin
def queue_command(box_id: str):
    cmd_type = request.form.get("type", "")
    if cmd_type not in ALLOWED_COMMANDS:
        return jsonify(error=f"invalid command: {cmd_type}"), 400
    exists = db().execute("SELECT 1 FROM boxes WHERE box_id=?", (box_id,)).fetchone()
    if not exists:
        return jsonify(error="unknown box"), 404
    db().execute(
        "INSERT INTO commands (box_id, type, created) VALUES (?,?,?)",
        (box_id, cmd_type, int(time.time())),
    )
    db().commit()
    return redirect(url_for("dashboard"))


@app.get("/boxes/<box_id>")
@require_admin
def box_detail(box_id: str):
    row = db().execute("SELECT * FROM boxes WHERE box_id=?", (box_id,)).fetchone()
    if not row:
        return "not found", 404
    status = json.loads(row["status_json"]) if row["status_json"] else {}
    cmds = db().execute(
        "SELECT * FROM commands WHERE box_id=? ORDER BY id DESC LIMIT 25", (box_id,)
    ).fetchall()
    return render_template("box.html", box_id=box_id, name=row["name"] or box_id,
                           status=status, cameras=status.get("cameras", []),
                           commands=cmds, allowed=ALLOWED_COMMANDS)


def main() -> None:
    if not ADMIN_PASSWORD:
        raise SystemExit("set FLEET_ADMIN_PASSWORD")
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("FLEET_PORT", "8080")))


if __name__ == "__main__":
    main()
