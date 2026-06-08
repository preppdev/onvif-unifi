"""Hardware-free verification of the gateway: config, SOAP surface, auth, XML."""
from __future__ import annotations

import base64
import hashlib
from xml.etree import ElementTree as ET

import pytest

from gateway.config import load_config
from gateway.mediamtx import build_paths
from gateway.models import StreamProfile, VirtualCamera, derive_mac
from gateway.onvif import discovery, soap, templates
from gateway.onvif.device import build_app


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def make_cam(**kw) -> VirtualCamera:
    main = StreamProfile("profile_main_1", "MainStream",
                         "rtsp://admin:pw@10.0.0.40/profile1/media.smp", "ch1_main")
    sub = StreamProfile("profile_sub_1", "SubStream",
                        "rtsp://admin:pw@10.0.0.40/profile1sub/media.smp", "ch1_sub",
                        width=640, height=480, framerate=15)
    defaults = dict(id=1, name="Front Door", ip="127.0.0.1", onvif_port=80,
                    rtsp_port=8554, main=main, sub=sub, username="admin", password="secret")
    defaults.update(kw)
    return VirtualCamera(**defaults)


@pytest.fixture
def cam():
    return make_cam()


@pytest.fixture
def client(cam):
    return build_app(cam).test_client()


def soap_post(client, action_body, *, auth=True, user="admin", pw="secret"):
    sec = ""
    if auth:
        nonce = b"randomnonce1"
        created = "2026-06-08T00:00:00Z"
        digest = base64.b64encode(
            hashlib.sha1(nonce + created.encode() + pw.encode()).digest()
        ).decode()
        sec = (
            '<s:Header><Security xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
            f"<UsernameToken><Username>{user}</Username>"
            f'<Password Type="#PasswordDigest">{digest}</Password>'
            f"<Nonce>{base64.b64encode(nonce).decode()}</Nonce>"
            f"<Created>{created}</Created></UsernameToken></Security></s:Header>"
        )
    env = (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"{sec}<s:Body>{action_body}</s:Body></s:Envelope>"
    )
    return client.post("/onvif/device_service", data=env,
                       content_type="application/soap+xml")


def body(local_name, ns="http://www.onvif.org/ver10/device/wsdl", inner=""):
    return f'<{local_name} xmlns="{ns}">{inner}</{local_name}>'


MEDIA_NS = "http://www.onvif.org/ver10/media/wsdl"


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def test_mac_is_deterministic_and_local():
    assert derive_mac("ch1-Front Door") == derive_mac("ch1-Front Door")
    assert derive_mac("a") != derive_mac("b")
    mac = derive_mac("x")
    assert mac.startswith("02:")
    assert len(mac.split(":")) == 6


def test_stream_uri_points_at_virtual_ip(cam):
    assert cam.rtsp_url(cam.main) == "rtsp://127.0.0.1:8554/ch1_main"
    assert "10.0.0.40" not in cam.rtsp_url(cam.main)  # not the encoder


def test_vnic_name_within_iface_limit():
    assert len(make_cam(id=16).vnic_name) <= 15


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_example_config_loads(tmp_path):
    cfg = load_config("config.example.yaml")
    assert len(cfg.cameras) == 16
    assert all(c.mac.startswith("02:") for c in cfg.cameras)
    assert len({c.ip for c in cfg.cameras}) == 16  # unique IPs


def test_duplicate_ip_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "encoder: {host: 1.2.3.4}\n"
        "cameras:\n  - {id: 1, name: A, ip: 10.0.0.1}\n  - {id: 2, name: B, ip: 10.0.0.1}\n"
    )
    with pytest.raises(ValueError, match="duplicate camera ip"):
        load_config(str(p))


# --------------------------------------------------------------------------- #
# soap parsing + auth
# --------------------------------------------------------------------------- #
def test_action_name():
    env = (b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
           b'<s:Body><GetProfiles xmlns="x"/></s:Body></s:Envelope>')
    assert soap.action_name(env) == "GetProfiles"
    assert soap.action_name(b"not xml") is None


def test_profile_and_config_token():
    assert soap.profile_token(b"<ProfileToken>profile_main_1</ProfileToken>") == "profile_main_1"
    assert soap.config_token(b"<trt:ConfigurationToken>VEnc_x</trt:ConfigurationToken>") == "VEnc_x"


def test_verify_auth_digest_text_and_failure():
    nonce_raw = b"abc"
    created = "2026-01-01T00:00:00Z"
    digest = base64.b64encode(hashlib.sha1(nonce_raw + created.encode() + b"secret").digest()).decode()
    good = (
        f"<UsernameToken><Username>admin</Username>"
        f'<Password Type="#PasswordDigest">{digest}</Password>'
        f"<Nonce>{base64.b64encode(nonce_raw).decode()}</Nonce>"
        f"<Created>{created}</Created></UsernameToken>"
    ).encode()
    assert soap.verify_auth(good, "admin", "secret") is True
    assert soap.verify_auth(good, "admin", "wrong") is False
    text = (b"<UsernameToken><Username>admin</Username>"
            b'<Password Type="PasswordText">secret</Password></UsernameToken>')
    assert soap.verify_auth(text, "admin", "secret") is True
    assert soap.verify_auth(b"<nothing/>", "admin", "secret") is False


# --------------------------------------------------------------------------- #
# templates produce well-formed XML
# --------------------------------------------------------------------------- #
def test_all_templates_are_valid_xml(cam):
    builders = [
        templates.system_date_and_time(__import__("time").gmtime()),
        templates.device_information(cam),
        templates.capabilities(cam),
        templates.services(cam),
        templates.scopes(cam),
        templates.network_interfaces(cam),
        templates.profiles(cam),
        templates.get_profile(cam, "profile_main_1"),
        templates.stream_uri(cam.rtsp_url(cam.main)),
        templates.snapshot_uri("http://x/snap"),
        templates.service_capabilities_device(),
        templates.service_capabilities_media(),
        templates.video_sources(cam),
        templates.video_source_configurations(cam),
        templates.video_source_configuration(cam),
        templates.video_encoder_configurations(cam),
        templates.video_encoder_configuration(cam, "VEnc_profile_main_1"),
        templates.video_encoder_configuration_options(cam),
        templates.empty_response("SetVideoEncoderConfiguration", "trt"),
        templates.event_properties(),
        templates.create_pullpoint("http://x/sub/1", "t", "t2"),
        templates.pull_messages_empty("t", "t2"),
        templates.unsubscribe(),
        templates.renew("t2"),
        templates.fault("nope"),
        discovery._probe_match(cam, "urn:uuid:test"),
    ]
    for xml in builders:
        ET.fromstring(xml)  # raises if malformed


# --------------------------------------------------------------------------- #
# device service behaviour
# --------------------------------------------------------------------------- #
def test_anonymous_datetime_allowed(client):
    r = soap_post(client, body("GetSystemDateAndTime"), auth=False)
    assert r.status_code == 200
    assert b"UTCDateTime" in r.data


def test_device_info_requires_auth(client, cam):
    assert soap_post(client, body("GetDeviceInformation"), auth=False).status_code == 401
    r = soap_post(client, body("GetDeviceInformation"), auth=True)
    assert r.status_code == 200
    assert cam.serial.encode() in r.data


def test_wrong_password_rejected(client):
    assert soap_post(client, body("GetDeviceInformation"), pw="bad").status_code == 401


def test_get_stream_uri_returns_virtual_ip(client):
    b = body("GetStreamUri", MEDIA_NS, "<ProfileToken>profile_main_1</ProfileToken>")
    r = soap_post(client, b)
    assert r.status_code == 200
    assert b"rtsp://127.0.0.1:8554/ch1_main" in r.data


@pytest.mark.parametrize("action,ns", [
    ("GetProfiles", MEDIA_NS),
    ("GetVideoSources", MEDIA_NS),
    ("GetVideoEncoderConfigurations", MEDIA_NS),
    ("GetVideoEncoderConfigurationOptions", MEDIA_NS),
    ("GetServiceCapabilities", MEDIA_NS),
    ("GetCapabilities", "http://www.onvif.org/ver10/device/wsdl"),
    ("GetServices", "http://www.onvif.org/ver10/device/wsdl"),
    ("GetScopes", "http://www.onvif.org/ver10/device/wsdl"),
    ("GetNetworkInterfaces", "http://www.onvif.org/ver10/device/wsdl"),
    ("GetEventProperties", "http://www.onvif.org/ver10/events/wsdl"),
    ("CreatePullPointSubscription", "http://www.onvif.org/ver10/events/wsdl"),
])
def test_supported_actions_return_valid_xml(client, action, ns):
    r = soap_post(client, body(action, ns))
    assert r.status_code == 200, action
    ET.fromstring(r.data)


def test_set_operations_are_noop_success(client):
    r = soap_post(client, body("SetVideoEncoderConfiguration", MEDIA_NS))
    assert r.status_code == 200
    assert b"SetVideoEncoderConfigurationResponse" in r.data


def test_unknown_action_faults(client):
    r = soap_post(client, body("GetTotallyMadeUpThing", MEDIA_NS))
    assert r.status_code == 400
    assert b"not supported" in r.data


# --------------------------------------------------------------------------- #
# mediamtx
# --------------------------------------------------------------------------- #
def test_build_paths_copy_vs_transcode(cam):
    paths = build_paths([cam], rtsp_port=8554)
    assert "ch1_main" in paths and "ch1_sub" in paths
    assert paths["ch1_main"]["source"].startswith("rtsp://admin:pw@10.0.0.40")
    assert paths["ch1_main"]["sourceOnDemand"] is True
    cam.sub.transcode = True
    paths = build_paths([cam], rtsp_port=8554)
    assert "runOnDemand" in paths["ch1_sub"]
    assert "source" not in paths["ch1_sub"]
