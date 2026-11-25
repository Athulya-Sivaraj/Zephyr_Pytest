"""Testbed configuration: one YAML file, overridable from the command line and env.

Precedence (highest first): CLI option > environment variable > testbed YAML > default.
Secrets (PSK, MQTT password) should come from env or a local, uncommitted YAML.
"""
from __future__ import annotations

import dataclasses
import ipaddress
import os
import socket
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SecuredApConfig:
    ssid: str = ''
    psk: str = field(default='', repr=False)
    bssid: str = ''


@dataclass
class WifiConfig:
    ssid: str = ''
    psk: str = field(default='', repr=False)
    bssid: str = ''
    open_ssid: str = ''
    open_bssid: str = ''
    secondary: SecuredApConfig = field(default_factory=SecuredApConfig)
    connect_timeout_s: float = 90.0
    # Settle time after "IP configuration applied". Kept configurable because the
    # firmware currently needs it; drop to 0 once a "link ready" event exists.
    post_connect_delay_s: float = 10.0


@dataclass
class PeerConfig:
    # 'local'   -> the suite starts TCP/UDP echo servers on this PC (recommended)
    # 'external'-> use an already running server at ip/base_port (no push/close modes)
    mode: str = 'local'
    ip: str = ''                 # empty + local mode -> auto-detect the PC's LAN IP
    bind: str = '0.0.0.0'
    tcp_base_port: int = 10001   # socket id N uses tcp_base_port + N
    udp_base_port: int = 10101
    max_sockets: int = 4


@dataclass
class DnsConfig:
    host: str = 'example.com'
    extra_hosts: list[str] = field(default_factory=lambda: ['google.com', 'yahoo.com'])


@dataclass
class MqttConfig:
    host: str = ''
    port: int = 1883
    tls_port: int = 0            # 0 -> TLS tests skipped
    topic_prefix: str = 'pnet/pytest'
    client_id: str = 'pnet-pytest'
    verify_with_host_client: bool = True   # needs paho-mqtt on the PC


@dataclass
class PerfConfig:
    iperf_path: str = ''         # iperf 2 binary (zperf is iperf2-compatible, NOT iperf3)
    udp_port: int = 5001
    tcp_port: int = 5001
    duration_s: int = 10
    min_udp_kbps: float = 0.0    # 0 -> record only, no threshold
    min_tcp_kbps: float = 0.0


@dataclass
class PowerSaveConfig:
    listen_intervals: list[int] = field(default_factory=lambda: [1, 3, 10])
    idle_before_wake_s: float = 10.0


@dataclass
class TestbedConfig:
    name: str = 'default'
    expected_fw_version: str = ''      # substring expected in "WiFi firmware version:"
    shell_timeout_s: float = 60.0
    wifi: WifiConfig = field(default_factory=WifiConfig)
    peer: PeerConfig = field(default_factory=PeerConfig)
    dns: DnsConfig = field(default_factory=DnsConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    perf: PerfConfig = field(default_factory=PerfConfig)
    ps: PowerSaveConfig = field(default_factory=PowerSaveConfig)
    # Optional lab equipment the suite may use; tests needing them are skipped if absent.
    capabilities: list[str] = field(default_factory=list)   # e.g. ap_control, ppk2, ncp_reset
    # Soak/stress scaling knobs
    soak_duration_s: int = 300
    stress_cycles: int = 200

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


def _merge(obj: Any, data: dict) -> None:
    for f in fields(obj):
        if f.name not in data:
            continue
        current = getattr(obj, f.name)
        if dataclasses.is_dataclass(current):
            _merge(current, data[f.name] or {})
        else:
            setattr(obj, f.name, data[f.name])


# CLI option name -> (dotted config path, env var, type)
OVERRIDES = {
    'wifi-ssid': ('wifi.ssid', 'PNET_SSID', str),
    'wifi-psk': ('wifi.psk', 'PNET_PSK', str),
    'wifi-bssid': ('wifi.bssid', 'PNET_BSSID', str),
    'wifi-secondary-psk': ('wifi.secondary.psk', 'PNET_SECONDARY_PSK', str),
    'peer-ip': ('peer.ip', 'PNET_SERVER_IP', str),
    'peer-port': ('peer.tcp_base_port', 'PNET_TCP_PORT', int),
    'peer-mode': ('peer.mode', 'PNET_PEER_MODE', str),
    'dns-host': ('dns.host', 'PNET_DNS_HOST', str),
    'mqtt-host': ('mqtt.host', 'PNET_MQTT_HOST', str),
    'mqtt-port': ('mqtt.port', 'PNET_MQTT_PORT', int),
    'expected-fw': ('expected_fw_version', 'PNET_EXPECTED_FW', str),
    'soak-duration-s': ('soak_duration_s', 'PNET_SOAK_DURATION_S', int),
    'stress-cycles': ('stress_cycles', 'PNET_STRESS_CYCLES', int),
}


def _set_path(cfg: TestbedConfig, dotted: str, value: Any) -> None:
    *parents, leaf = dotted.split('.')
    target = cfg
    for p in parents:
        target = getattr(target, p)
    setattr(target, leaf, value)


def detect_local_ip() -> str:
    """LAN IP of this PC as seen by the DUT's network (no packet is actually sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(('10.255.255.255', 1))
            return s.getsockname()[0]
        except OSError:
            return '127.0.0.1'


def load_config(path: str | None, cli: dict[str, Any]) -> TestbedConfig:
    cfg = TestbedConfig()
    if path:
        data = yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
        _merge(cfg, data)
    for opt, (dotted, env, typ) in OVERRIDES.items():
        value = cli.get(opt)
        if value is None and os.environ.get(env):
            value = os.environ[env]
        if value is not None:
            _set_path(cfg, dotted, typ(value))
    caps = os.environ.get('PNET_CAPABILITIES')
    if caps:
        cfg.capabilities = sorted(set(cfg.capabilities) | {c.strip() for c in caps.split(',') if c.strip()})

    if cfg.peer.mode not in ('local', 'external'):
        raise ValueError(f"peer.mode must be 'local' or 'external', got {cfg.peer.mode!r}")
    if not cfg.peer.ip and cfg.peer.mode == 'local':
        cfg.peer.ip = detect_local_ip()
    if cfg.peer.ip:
        ipaddress.IPv4Address(cfg.peer.ip)
    for port in (cfg.peer.tcp_base_port, cfg.peer.udp_base_port):
        if not 1 <= port <= 65535 - cfg.peer.max_sockets:
            raise ValueError(f'port {port} out of range')
    return cfg
