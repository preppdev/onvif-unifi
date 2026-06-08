"""Flask app implementing one virtual camera's ONVIF Device + Media services."""
from __future__ import annotations

import logging
import time
import uuid as _uuid

from flask import Flask, Response, request

from ..models import VirtualCamera
from . import soap, templates

log = logging.getLogger("onvif.device")

_XML = "application/soap+xml; charset=utf-8"

# Operations callable without authentication (ONVIF spec requires these open so a
# client can sync its clock and compute the PasswordDigest before authenticating).
_ANON_ACTIONS = {"GetSystemDateAndTime", "GetWsdlUrl"}

# Set*/control operations we accept and no-op so UniFi's config push succeeds.
# Mapped to the namespace prefix used in the matching <...Response/> element.
_NOOP_ACTIONS = {
    "SetVideoEncoderConfiguration": "trt",
    "SetVideoSourceConfiguration": "trt",
    "SetSynchronizationPoint": "trt",
    "SetSystemDateAndTime": "tds",
    "SetSystemFactoryDefault": "tds",
    "SetScopes": "tds",
    "SetHostname": "tds",
    "SystemReboot": "tds",
}


def _iso(offset_s: int = 0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_s))


def _resp(xml: str, status: int = 200) -> Response:
    return Response(xml, status=status, mimetype=_XML)


def build_app(cam: VirtualCamera) -> Flask:
    app = Flask(f"onvif-cam-{cam.id}")
    app.logger.disabled = True

    profiles_by_token = {p.token: p for p in cam.profiles()}

    def handle(body: bytes) -> Response:
        action = soap.action_name(body)
        if action is None:
            return _resp(templates.fault("malformed request", "ter:WellFormed"), 400)

        if action not in _ANON_ACTIONS:
            if not soap.verify_auth(body, cam.username, cam.password):
                log.debug("cam%s: auth failed for %s", cam.id, action)
                return _resp(templates.fault("Sender not authorized"), 401)

        log.debug("cam%s: %s", cam.id, action)

        if action == "GetSystemDateAndTime":
            return _resp(templates.system_date_and_time(time.gmtime()))
        if action == "GetDeviceInformation":
            return _resp(templates.device_information(cam))
        if action == "GetCapabilities":
            return _resp(templates.capabilities(cam))
        if action == "GetServices":
            return _resp(templates.services(cam))
        if action == "GetScopes":
            return _resp(templates.scopes(cam))
        if action == "GetNetworkInterfaces":
            return _resp(templates.network_interfaces(cam))
        if action in ("GetProfiles", "GetProfile"):
            return _resp(templates.profiles(cam))
        if action == "GetStreamUri":
            token = soap.profile_token(body)
            prof = profiles_by_token.get(token) or cam.main
            return _resp(templates.stream_uri(cam.rtsp_url(prof)))
        if action == "GetSnapshotUri":
            uri = f"http://{cam.ip}:{cam.onvif_port}/onvif/snapshot"
            return _resp(templates.snapshot_uri(uri))

        # --- Media configuration surface (queried during adoption) ----------
        if action == "GetServiceCapabilities":
            # Ambiguous between device/media; both shapes are harmless to a client.
            return _resp(templates.service_capabilities_media())
        if action == "GetVideoSources":
            return _resp(templates.video_sources(cam))
        if action == "GetVideoSourceConfigurations":
            return _resp(templates.video_source_configurations(cam))
        if action == "GetVideoSourceConfiguration":
            return _resp(templates.video_source_configuration(cam))
        if action == "GetVideoEncoderConfigurations":
            return _resp(templates.video_encoder_configurations(cam))
        if action == "GetVideoEncoderConfiguration":
            return _resp(templates.video_encoder_configuration(cam, soap.config_token(body)))
        if action == "GetVideoEncoderConfigurationOptions":
            return _resp(templates.video_encoder_configuration_options(cam))

        # --- Events (minimal pull-point; no events emitted yet) -------------
        if action == "GetEventProperties":
            return _resp(templates.event_properties())
        if action == "CreatePullPointSubscription":
            sub_id = _uuid.uuid4().hex[:12]
            sub_url = f"http://{cam.ip}:{cam.onvif_port}/onvif/subscription/{sub_id}"
            return _resp(templates.create_pullpoint(sub_url, _iso(), _iso(60)))
        if action == "PullMessages":
            return _resp(templates.pull_messages_empty(_iso(), _iso(60)))
        if action == "Renew":
            return _resp(templates.renew(_iso(60)))
        if action in ("Unsubscribe", "TopicFilter"):
            return _resp(templates.unsubscribe())

        # --- No-op Set*/control ops so UniFi's config push doesn't fault ----
        if action in _NOOP_ACTIONS:
            return _resp(templates.empty_response(action, _NOOP_ACTIONS[action]))

        # Unknown/unsupported operation — log it so we can extend coverage.
        log.info("cam%s: unsupported ONVIF action %r", cam.id, action)
        return _resp(templates.fault(f"Action {action} not supported", "ter:ActionNotSupported"), 400)

    @app.route("/onvif/device_service", methods=["POST"])
    @app.route("/onvif/media_service", methods=["POST"])
    @app.route("/onvif/events_service", methods=["POST"])
    @app.route("/onvif/subscription/<sub_id>", methods=["POST"])
    @app.route("/", methods=["POST"])
    def soap_endpoint(sub_id=None):
        return handle(request.get_data())

    @app.route("/onvif/snapshot", methods=["GET"])
    def snapshot():
        from .snapshot import capture_jpeg

        jpeg = capture_jpeg(cam.rtsp_url(cam.main))
        if jpeg is None:
            return Response("snapshot unavailable", status=503)
        return Response(jpeg, mimetype="image/jpeg")

    @app.route("/healthz", methods=["GET"])
    def healthz():
        return {"camera": cam.id, "name": cam.name, "ip": cam.ip}

    return app
