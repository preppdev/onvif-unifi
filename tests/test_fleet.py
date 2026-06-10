"""Tests for the fleet server config-push flow and the agent bootstrap/identity."""
from __future__ import annotations

import base64
import os

os.environ.setdefault("FLEET_ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FLEET_ENROLL_KEY", "enroll123")
os.environ.setdefault("FLEET_DB", "/tmp/fleet_pytest_import.db")

import pytest

from fleet import server

ADMIN = {"Authorization": "Basic " + base64.b64encode(b"admin:admin123").decode()}
BOX = {"Authorization": "Bearer key-1", "X-Enroll-Key": "enroll123"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", str(tmp_path / "f.db"))
    server.init_db()
    return server.app.test_client()


def _enroll(client, box_id="box-1"):
    return client.post("/api/heartbeat",
                       json={"box_id": box_id, "hostname": "h", "applied_version": ""},
                       headers=BOX)


# --- server: config push --------------------------------------------------- #
def test_unprovisioned_box_gets_no_config(client):
    r = _enroll(client)
    assert r.status_code == 200
    body = r.get_json()
    assert "config" not in body and body.get("config_version") is None


def test_set_config_then_pushed_until_applied(client):
    _enroll(client)
    cfg = "encoder:\n  host: 10.0.0.1\ncameras: []\n"
    r = client.post("/boxes/box-1/config", data={"config": cfg}, headers=ADMIN)
    assert r.status_code == 302  # redirect back to detail

    # Box hasn't applied anything yet -> server hands over the config + version.
    body = _enroll(client).get_json()
    assert body["config"] == cfg
    version = body["config_version"]
    assert version and len(version) == 12

    # Box reports it applied that version -> server stops pushing the body.
    r = client.post("/api/heartbeat",
                    json={"box_id": "box-1", "applied_version": version},
                    headers={"Authorization": "Bearer key-1"})
    body = r.get_json()
    assert body["config_version"] == version
    assert "config" not in body  # in sync, nothing to push


def test_set_config_unknown_box_404(client):
    r = client.post("/boxes/ghost/config", data={"config": "x"}, headers=ADMIN)
    assert r.status_code == 404


def test_dashboard_shows_provisioning_states(client):
    _enroll(client, "box-unprov")
    _enroll(client, "box-prov")
    cfg = "cameras: []\n"
    client.post("/boxes/box-prov/config", data={"config": cfg}, headers=ADMIN)
    version = _enroll(client, "box-prov").get_json()["config_version"]
    client.post("/api/heartbeat", json={"box_id": "box-prov", "applied_version": version},
                headers={"Authorization": "Bearer key-1"})
    html = client.get("/", headers=ADMIN).get_data(as_text=True)
    assert "unprovisioned" in html and "provisioned" in html


# --- agent: bootstrap + identity ------------------------------------------- #
def test_load_fleet_dedicated_file(tmp_path):
    from gateway.agent import load_fleet

    p = tmp_path / "fleet.yaml"
    p.write_text("server_url: http://fleet:8080/\nenroll_key: k\ninterval: 30\n")
    fc = load_fleet(str(p))
    assert fc.server_url == "http://fleet:8080"  # trailing slash stripped
    assert fc.enroll_key == "k" and fc.interval == 30 and fc.enabled


def test_load_fleet_gateway_block_style(tmp_path):
    from gateway.agent import load_fleet

    p = tmp_path / "gateway.yaml"
    p.write_text("encoder: {host: x}\nfleet:\n  server_url: http://h:8080\n  enabled: true\n")
    fc = load_fleet(str(p))
    assert fc.server_url == "http://h:8080" and fc.enabled


def test_enroll_endpoint(client, monkeypatch):
    monkeypatch.setattr(server, "PROVISION_TOKEN", "secret-tok")
    monkeypatch.setattr(server, "PROVISION_TSKEY", "tskey-auth-xyz")
    monkeypatch.setattr(server, "ENROLL_KEY", "enroll123")
    # wrong/missing token -> 404 (don't reveal the endpoint)
    assert client.get("/enroll/nope").status_code == 404
    # correct token -> a bootstrap script with the baked params
    r = client.get("/enroll/secret-tok")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "tskey-auth-xyz" in body and "FLEET_ENROLL_KEY='enroll123'" in body
    assert "install.sh" in body and body.startswith("#!/usr/bin/env bash")


def test_enroll_disabled_without_token(client, monkeypatch):
    monkeypatch.setattr(server, "PROVISION_TOKEN", "")
    assert client.get("/enroll/anything").status_code == 404


def test_derive_box_id_is_stable_and_formatted():
    from gateway.agent import derive_box_id

    a, b = derive_box_id(), derive_box_id()
    assert a == b and a.startswith("onvif-") and len(a) == len("onvif-") + 12
