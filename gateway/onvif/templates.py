"""SOAP/XML response builders for the ONVIF Device + Media services.

Hand-rolled XML (not zeep) is deliberate: the response surface a Profile-S NVR
like UniFi actually calls is small and fixed, and raw templates are far easier to
keep byte-stable than a generated WSDL stack. Every response shares one envelope.
"""
from __future__ import annotations

from ..models import StreamProfile, VirtualCamera

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "tev": "http://www.onvif.org/ver10/events/wsdl",
}


def envelope(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
        'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
        'xmlns:tev="http://www.onvif.org/ver10/events/wsdl" '
        'xmlns:tt="http://www.onvif.org/ver10/schema">'
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )


def fault(reason: str, subcode: str = "ter:NotAuthorized") -> str:
    return envelope(
        '<s:Fault xmlns:ter="http://www.onvif.org/ver10/error">'
        f"<s:Code><s:Value>s:Sender</s:Value><s:Subcode><s:Value>{subcode}</s:Value></s:Subcode></s:Code>"
        f"<s:Reason><s:Text xml:lang=\"en\">{reason}</s:Text></s:Reason></s:Fault>"
    )


def system_date_and_time(utc_struct) -> str:
    return envelope(
        "<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime>"
        "<tt:DateTimeType>NTP</tt:DateTimeType><tt:DaylightSavings>false</tt:DaylightSavings>"
        "<tt:TimeZone><tt:TZ>UTC0</tt:TZ></tt:TimeZone>"
        "<tt:UTCDateTime>"
        f"<tt:Time><tt:Hour>{utc_struct.tm_hour}</tt:Hour><tt:Minute>{utc_struct.tm_min}</tt:Minute><tt:Second>{utc_struct.tm_sec}</tt:Second></tt:Time>"
        f"<tt:Date><tt:Year>{utc_struct.tm_year}</tt:Year><tt:Month>{utc_struct.tm_mon}</tt:Month><tt:Day>{utc_struct.tm_mday}</tt:Day></tt:Date>"
        "</tt:UTCDateTime></tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>"
    )


def device_information(cam: VirtualCamera) -> str:
    return envelope(
        "<tds:GetDeviceInformationResponse>"
        "<tds:Manufacturer>ONVIF-Gateway</tds:Manufacturer>"
        f"<tds:Model>SPE-1630-CH{cam.id}</tds:Model>"
        "<tds:FirmwareVersion>0.1.0</tds:FirmwareVersion>"
        f"<tds:SerialNumber>{cam.serial}</tds:SerialNumber>"
        f"<tds:HardwareId>{cam.serial}</tds:HardwareId>"
        "</tds:GetDeviceInformationResponse>"
    )


def capabilities(cam: VirtualCamera) -> str:
    base = cam.device_service_url
    media = f"http://{cam.ip}:{cam.onvif_port}/onvif/media_service"
    events = f"http://{cam.ip}:{cam.onvif_port}/onvif/events_service"
    return envelope(
        "<tds:GetCapabilitiesResponse><tds:Capabilities>"
        f'<tt:Device><tt:XAddr>{base}</tt:XAddr>'
        "<tt:Network><tt:IPFilter>false</tt:IPFilter><tt:ZeroConfiguration>false</tt:ZeroConfiguration>"
        "<tt:IPVersion6>false</tt:IPVersion6><tt:DynDNS>false</tt:DynDNS></tt:Network>"
        "<tt:System><tt:DiscoveryResolve>false</tt:DiscoveryResolve><tt:DiscoveryBye>true</tt:DiscoveryBye>"
        "<tt:RemoteDiscovery>false</tt:RemoteDiscovery><tt:SystemBackup>false</tt:SystemBackup>"
        "<tt:SystemLogging>false</tt:SystemLogging><tt:FirmwareUpgrade>false</tt:FirmwareUpgrade></tt:System></tt:Device>"
        f'<tt:Events><tt:XAddr>{events}</tt:XAddr><tt:WSSubscriptionPolicySupport>true</tt:WSSubscriptionPolicySupport>'
        "<tt:WSPullPointSupport>true</tt:WSPullPointSupport><tt:WSPausableSubscriptionManagerInterfaceSupport>false</tt:WSPausableSubscriptionManagerInterfaceSupport></tt:Events>"
        f'<tt:Media><tt:XAddr>{media}</tt:XAddr>'
        "<tt:StreamingCapabilities><tt:RTPMulticast>false</tt:RTPMulticast><tt:RTP_TCP>true</tt:RTP_TCP>"
        "<tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP></tt:StreamingCapabilities></tt:Media>"
        "</tds:Capabilities></tds:GetCapabilitiesResponse>"
    )


def services(cam: VirtualCamera) -> str:
    base = cam.device_service_url
    media = f"http://{cam.ip}:{cam.onvif_port}/onvif/media_service"
    events = f"http://{cam.ip}:{cam.onvif_port}/onvif/events_service"
    return envelope(
        "<tds:GetServicesResponse>"
        f"<tds:Service><tds:Namespace>{NS['tds']}</tds:Namespace><tds:XAddr>{base}</tds:XAddr>"
        "<tds:Version><tt:Major>2</tt:Major><tt:Minor>5</tt:Minor></tds:Version></tds:Service>"
        f"<tds:Service><tds:Namespace>{NS['trt']}</tds:Namespace><tds:XAddr>{media}</tds:XAddr>"
        "<tds:Version><tt:Major>2</tt:Major><tt:Minor>5</tt:Minor></tds:Version></tds:Service>"
        f"<tds:Service><tds:Namespace>{NS['tev']}</tds:Namespace><tds:XAddr>{events}</tds:XAddr>"
        "<tds:Version><tt:Major>2</tt:Major><tt:Minor>5</tt:Minor></tds:Version></tds:Service>"
        "</tds:GetServicesResponse>"
    )


def scopes(cam: VirtualCamera) -> str:
    items = [
        "onvif://www.onvif.org/type/video_encoder",
        "onvif://www.onvif.org/Profile/Streaming",
        f"onvif://www.onvif.org/name/{cam.name.replace(' ', '_')}",
        f"onvif://www.onvif.org/hardware/SPE-1630-CH{cam.id}",
    ]
    body = "".join(
        f"<tt:Scope><tt:ScopeDef>Fixed</tt:ScopeDef><tt:ScopeItem>{s}</tt:ScopeItem></tt:Scope>"
        for s in items
    )
    return envelope(f"<tds:GetScopesResponse>{body}</tds:GetScopesResponse>")


def network_interfaces(cam: VirtualCamera) -> str:
    prefix = sum(bin(int(o)).count("1") for o in cam.netmask.split("."))
    return envelope(
        "<tds:GetNetworkInterfacesResponse>"
        f'<tds:NetworkInterfaces token="eth0"><tt:Enabled>true</tt:Enabled>'
        f"<tt:Info><tt:Name>eth0</tt:Name><tt:HwAddress>{cam.mac}</tt:HwAddress><tt:MTU>1500</tt:MTU></tt:Info>"
        "<tt:IPv4><tt:Enabled>true</tt:Enabled><tt:Config>"
        f"<tt:Manual><tt:Address>{cam.ip}</tt:Address><tt:PrefixLength>{prefix}</tt:PrefixLength></tt:Manual>"
        "<tt:DHCP>false</tt:DHCP></tt:Config></tt:IPv4>"
        "</tds:NetworkInterfaces></tds:GetNetworkInterfacesResponse>"
    )


def _video_encoder_config(p: StreamProfile) -> str:
    enc = "H265" if p.codec.upper() in ("H265", "HEVC") else "H264"
    return (
        f'<tt:VideoEncoderConfiguration token="VEnc_{p.token}">'
        f"<tt:Name>{p.name}</tt:Name><tt:UseCount>1</tt:UseCount>"
        f"<tt:Encoding>{enc}</tt:Encoding>"
        f"<tt:Resolution><tt:Width>{p.width}</tt:Width><tt:Height>{p.height}</tt:Height></tt:Resolution>"
        "<tt:Quality>5</tt:Quality>"
        f"<tt:RateControl><tt:FrameRateLimit>{p.framerate}</tt:FrameRateLimit><tt:EncodingInterval>1</tt:EncodingInterval>"
        f"<tt:BitrateLimit>{p.bitrate}</tt:BitrateLimit></tt:RateControl>"
        f"<tt:{enc}><tt:GovLength>{p.framerate}</tt:GovLength><tt:{enc}Profile>Main</tt:{enc}Profile></tt:{enc}>"
        "<tt:Multicast><tt:Address><tt:Type>IPv4</tt:Type><tt:IPv4Address>0.0.0.0</tt:IPv4Address></tt:Address>"
        "<tt:Port>0</tt:Port><tt:TTL>1</tt:TTL><tt:AutoStart>false</tt:AutoStart></tt:Multicast>"
        "<tt:SessionTimeout>PT60S</tt:SessionTimeout>"
        "</tt:VideoEncoderConfiguration>"
    )


def _video_source_config(cam: VirtualCamera, p: StreamProfile) -> str:
    return (
        f'<tt:VideoSourceConfiguration token="VSrc_{cam.id}">'
        f"<tt:Name>VideoSource</tt:Name><tt:UseCount>1</tt:UseCount>"
        f"<tt:SourceToken>VideoSourceToken_{cam.id}</tt:SourceToken>"
        f"<tt:Bounds x=\"0\" y=\"0\" width=\"{p.width}\" height=\"{p.height}\"/>"
        "</tt:VideoSourceConfiguration>"
    )


def _profile_block(cam: VirtualCamera, p: StreamProfile) -> str:
    return (
        f'<trt:Profiles token="{p.token}" fixed="true">'
        f"<tt:Name>{p.name}</tt:Name>"
        f"{_video_source_config(cam, p)}"
        f"{_video_encoder_config(p)}"
        "</trt:Profiles>"
    )


def profiles(cam: VirtualCamera) -> str:
    blocks = "".join(_profile_block(cam, p) for p in cam.profiles())
    return envelope(f"<trt:GetProfilesResponse>{blocks}</trt:GetProfilesResponse>")


def stream_uri(uri: str) -> str:
    return envelope(
        "<trt:GetStreamUriResponse><trt:MediaUri>"
        f"<tt:Uri>{uri}</tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
        "<tt:Timeout>PT0S</tt:Timeout>"
        "</trt:MediaUri></trt:GetStreamUriResponse>"
    )


def snapshot_uri(uri: str) -> str:
    return envelope(
        "<trt:GetSnapshotUriResponse><trt:MediaUri>"
        f"<tt:Uri>{uri}</tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
        "<tt:Timeout>PT0S</tt:Timeout>"
        "</trt:MediaUri></trt:GetSnapshotUriResponse>"
    )


def get_profile(cam: VirtualCamera, token: str | None) -> str:
    """GetProfile (singular) — return the one matching profile, or main."""
    target = next((p for p in cam.profiles() if p.token == token), cam.main)
    return envelope(f"<trt:GetProfileResponse>{_profile_block(cam, target)}</trt:GetProfileResponse>")


def service_capabilities_device() -> str:
    return envelope(
        "<tds:GetServiceCapabilitiesResponse><tds:Capabilities>"
        '<tds:Network IPFilter="false" ZeroConfiguration="false" IPVersion6="false" DynDNS="false"/>'
        '<tds:System DiscoveryResolve="false" DiscoveryBye="true" RemoteDiscovery="false" '
        'SystemBackup="false" SystemLogging="false" FirmwareUpgrade="false"/>'
        '<tds:Security TLS1.1="false" TLS1.2="false" OnboardKeyGeneration="false" '
        'AccessPolicyConfig="false" UsernameToken="true" HttpDigest="false"/>'
        "</tds:Capabilities></tds:GetServiceCapabilitiesResponse>"
    )


def service_capabilities_media() -> str:
    return envelope(
        "<trt:GetServiceCapabilitiesResponse><trt:Capabilities SnapshotUri=\"true\" Rotation=\"false\">"
        '<trt:ProfileCapabilities MaximumNumberOfProfiles="2"/>'
        '<trt:StreamingCapabilities RTPMulticast="false" RTP_TCP="true" RTP_RTSP_TCP="true"/>'
        "</trt:Capabilities></trt:GetServiceCapabilitiesResponse>"
    )


def video_sources(cam: VirtualCamera) -> str:
    p = cam.main
    return envelope(
        "<trt:GetVideoSourcesResponse>"
        f'<trt:VideoSources token="VideoSourceToken_{cam.id}">'
        f"<tt:Framerate>{p.framerate}</tt:Framerate>"
        f"<tt:Resolution><tt:Width>{p.width}</tt:Width><tt:Height>{p.height}</tt:Height></tt:Resolution>"
        "</trt:VideoSources></trt:GetVideoSourcesResponse>"
    )


def video_source_configurations(cam: VirtualCamera) -> str:
    return envelope(
        "<trt:GetVideoSourceConfigurationsResponse>"
        f"{_video_source_config(cam, cam.main)}"
        "</trt:GetVideoSourceConfigurationsResponse>"
    )


def video_source_configuration(cam: VirtualCamera) -> str:
    return envelope(
        "<trt:GetVideoSourceConfigurationResponse>"
        f"{_video_source_config(cam, cam.main)}"
        "</trt:GetVideoSourceConfigurationResponse>"
    )


def video_encoder_configurations(cam: VirtualCamera) -> str:
    blocks = "".join(_video_encoder_config(p) for p in cam.profiles())
    return envelope(
        f"<trt:GetVideoEncoderConfigurationsResponse>{blocks}</trt:GetVideoEncoderConfigurationsResponse>"
    )


def video_encoder_configuration(cam: VirtualCamera, token: str | None) -> str:
    target = next((p for p in cam.profiles() if f"VEnc_{p.token}" == token), cam.main)
    return envelope(
        "<trt:GetVideoEncoderConfigurationResponse>"
        f"{_video_encoder_config(target)}"
        "</trt:GetVideoEncoderConfigurationResponse>"
    )


def video_encoder_configuration_options(cam: VirtualCamera) -> str:
    p = cam.main
    enc = "H265" if p.codec.upper() in ("H265", "HEVC") else "H264"
    return envelope(
        "<trt:GetVideoEncoderConfigurationOptionsResponse><trt:Options>"
        "<tt:QualityRange><tt:Min>1</tt:Min><tt:Max>10</tt:Max></tt:QualityRange>"
        f"<tt:{enc}>"
        f"<tt:ResolutionsAvailable><tt:Width>{p.width}</tt:Width><tt:Height>{p.height}</tt:Height></tt:ResolutionsAvailable>"
        "<tt:GovLengthRange><tt:Min>1</tt:Min><tt:Max>60</tt:Max></tt:GovLengthRange>"
        f"<tt:FrameRateRange><tt:Min>1</tt:Min><tt:Max>{p.framerate}</tt:Max></tt:FrameRateRange>"
        "<tt:EncodingIntervalRange><tt:Min>1</tt:Min><tt:Max>1</tt:Max></tt:EncodingIntervalRange>"
        f"<tt:{enc}ProfilesSupported>Main</tt:{enc}ProfilesSupported>"
        f"</tt:{enc}>"
        "</trt:Options></trt:GetVideoEncoderConfigurationOptionsResponse>"
    )


def empty_response(action: str, ns_prefix: str = "trt") -> str:
    """Generic empty success body for no-op Set*/configuration operations."""
    return envelope(f"<{ns_prefix}:{action}Response/>")


# --- Events (minimal pull-point; no real events emitted yet) ----------------

def event_properties() -> str:
    return envelope(
        "<tev:GetEventPropertiesResponse>"
        "<tev:TopicNamespaceLocation>http://www.onvif.org/onvif/ver10/topics/topicns.xml</tev:TopicNamespaceLocation>"
        '<wsnt:FixedTopicSet xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2">true</wsnt:FixedTopicSet>'
        '<wstop:TopicSet xmlns:wstop="http://docs.oasis-open.org/wsn/t-1">'
        '<tns1:RuleEngine xmlns:tns1="http://www.onvif.org/ver10/topics">'
        "<CellMotionDetector><Motion></Motion></CellMotionDetector>"
        "</tns1:RuleEngine></wstop:TopicSet>"
        '<wsnt:TopicExpressionDialect xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2">'
        "http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet</wsnt:TopicExpressionDialect>"
        "</tev:GetEventPropertiesResponse>"
    )


def create_pullpoint(subscription_url: str, now_iso: str, term_iso: str) -> str:
    return envelope(
        "<tev:CreatePullPointSubscriptionResponse>"
        '<tev:SubscriptionReference xmlns:a="http://www.w3.org/2005/08/addressing">'
        f"<a:Address>{subscription_url}</a:Address></tev:SubscriptionReference>"
        f'<wsnt:CurrentTime xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2">{now_iso}</wsnt:CurrentTime>'
        f'<wsnt:TerminationTime xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2">{term_iso}</wsnt:TerminationTime>'
        "</tev:CreatePullPointSubscriptionResponse>"
    )


def pull_messages_empty(now_iso: str, term_iso: str) -> str:
    return envelope(
        "<tev:PullMessagesResponse>"
        f"<tev:CurrentTime>{now_iso}</tev:CurrentTime>"
        f"<tev:TerminationTime>{term_iso}</tev:TerminationTime>"
        "</tev:PullMessagesResponse>"
    )


def unsubscribe() -> str:
    return envelope(
        '<wsnt:UnsubscribeResponse xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2"/>'
    )


def renew(term_iso: str) -> str:
    return envelope(
        '<wsnt:RenewResponse xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2">'
        f"<wsnt:TerminationTime>{term_iso}</wsnt:TerminationTime>"
        "</wsnt:RenewResponse>"
    )
