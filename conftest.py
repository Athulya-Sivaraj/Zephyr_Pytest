"""Shared fixtures for the pnet eRPC/NCP Wi-Fi suite.

Fixture chain (all re-validate state, nothing is "cached online"):

    pnet (session)        UART shell, echo off, typed client
      initialized         ensures `pnet init` has run since the last reboot
        online            ensures Wi-Fi is connected with an IP
    peers / peer          host TCP+UDP servers (reset before every test)
    board_guard (auto)    after every test: cleanup, fatal-log check, recovery

Markers:
    @pytest.mark.scope_id("TCP-001")      links the test to the scope workbook and
                                          to known_issues.yaml (auto xfail)
    @pytest.mark.requires("tcp_peer", ...) skip unless the testbed provides it
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import pytest
import yaml

from pnet_lib.client import Pnet
from pnet_lib.config import OVERRIDES, TestbedConfig, load_config
from pnet_lib.servers import PeerPool
from pnet_lib.shell import FatalLogError, PnetShell, SyncLostError

logger = logging.getLogger(__name__)
HERE = Path(__file__).parent
SHELL_KEY = pytest.StashKey[PnetShell]()
CFG_KEY = pytest.StashKey[TestbedConfig]()


# ---------------------------------------------------------------------- options / config
def pytest_addoption(parser):
    group = parser.getgroup('pnet')
    group.addoption('--testbed', default=None, help='Testbed YAML (see testbed.example.yaml)')
    group.addoption('--known-issues', default=str(HERE / 'known_issues.yaml'),
                    help='YAML of scope ids expected to fail on this firmware')
    for opt in OVERRIDES:
        group.addoption(f'--{opt}', default=None, help=f'Override {OVERRIDES[opt][0]}')


def pytest_configure(config):
    cli = {opt: config.getoption(f'--{opt}') for opt in OVERRIDES}
    config.stash[CFG_KEY] = load_config(config.getoption('--testbed'), cli)


def pytest_report_header(config):
    cfg = config.stash[CFG_KEY]
    return (f'pnet testbed: {cfg.name} | peer {cfg.peer.mode} {cfg.peer.ip}:{cfg.peer.tcp_base_port} '
            f'| capabilities: {",".join(cfg.capabilities) or "-"}')


# ---------------------------------------------------------------------- known issues -> xfail
def _load_known(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
    return data.get('issues') or {}


def pytest_collection_modifyitems(config, items):
    known = _load_known(config.getoption('--known-issues'))
    for item in items:
        marker = item.get_closest_marker('scope_id')
        if not marker:
            continue
        sid = marker.args[0]
        item.user_properties.append(('scope_id', sid))
        param = getattr(getattr(item, 'callspec', None), 'id', None)
        entry = known.get(f'{sid}[{param}]') if param else None
        entry = entry or known.get(sid)
        if not entry:
            continue
        if isinstance(entry, str):
            entry = {'reason': entry}
        item.add_marker(pytest.mark.xfail(reason=f'{sid}: {entry["reason"]}',
                                          strict=entry.get('strict', True),
                                          run=entry.get('run', True)))


# ---------------------------------------------------------------------- requires(...)
def _available(cfg: TestbedConfig) -> dict[str, str]:
    """capability -> reason it is missing ('' when available)."""
    paho = importlib.util.find_spec('paho') is not None
    return {
        'wifi': '' if cfg.wifi.ssid else 'set wifi.ssid / --wifi-ssid',
        'secured_wifi': '' if cfg.wifi.ssid and cfg.wifi.psk else 'set wifi.ssid and wifi.psk',
        'secondary_wifi': ('' if cfg.wifi.secondary.ssid and cfg.wifi.secondary.psk
                           else 'set wifi.secondary.ssid and wifi.secondary.psk'),
        'secondary_bssid': '' if cfg.wifi.secondary.bssid else 'set wifi.secondary.bssid',
        'tcp_peer': '' if cfg.peer.ip else 'no peer IP',
        'local_peer': '' if cfg.peer.mode == 'local' else 'needs peer.mode=local (server-side control)',
        'bssid': '' if cfg.wifi.bssid else 'set wifi.bssid / --wifi-bssid',
        'open_network': '' if cfg.wifi.open_ssid else 'set wifi.open_ssid',
        'open_bssid': '' if cfg.wifi.open_bssid else 'set wifi.open_bssid',
        'mqtt': '' if cfg.mqtt.host else 'set mqtt.host / --mqtt-host',
        'mqtt_tls': '' if cfg.mqtt.host and cfg.mqtt.tls_port else 'set mqtt.tls_port',
        'host_mqtt': '' if paho and cfg.mqtt.verify_with_host_client else 'pip install paho-mqtt',
        'iperf': '' if cfg.perf.iperf_path else 'set perf.iperf_path (iperf 2)',
        **{c: '' for c in cfg.capabilities},
    }


def pytest_runtest_setup(item):
    cfg = item.config.stash[CFG_KEY]
    avail = _available(cfg)
    for marker in item.iter_markers('requires'):
        for need in marker.args:
            reason = avail.get(need, f'testbed capability {need!r} not listed')
            if reason:
                pytest.skip(f'requires {need}: {reason}')


# ---------------------------------------------------------------------- UART log on failure
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        sh = item.config.stash.get(SHELL_KEY, None)
        if sh is not None:
            report.sections.append(('UART (last 200 lines)', '\n'.join(list(sh.history)[-200:])))


# ---------------------------------------------------------------------- core fixtures
@pytest.fixture(scope='session')
def testbed(pytestconfig) -> TestbedConfig:
    return pytestconfig.stash[CFG_KEY]


@pytest.fixture(scope='session')
def pnet(shell, dut, testbed, pytestconfig) -> Pnet:
    """Session-wide client. Needs --dut-scope=session (set in pytest.ini)."""
    sh = PnetShell(dut, timeout=testbed.shell_timeout_s)
    pytestconfig.stash[SHELL_KEY] = sh
    sh.expect_reboot = True        # the boot banner may still be buffered at session start
    sh.drain()
    sh.expect_reboot = False
    sh.echo_off()
    client = Pnet(sh, timeout=testbed.shell_timeout_s)
    client.board_dead = False
    yield client
    try:
        if sh.synchronized:
            sh.exec('shell echo on')
        else:
            dut.write(b'shell echo on\n')
    except Exception:
        pass


@pytest.fixture
def initialized(pnet: Pnet) -> Pnet:
    if not pnet.state.initialized:
        pnet.init()
        pnet.fw_version()          # eRPC round-trip sanity check
    return pnet


@pytest.fixture
def online(initialized: Pnet, testbed: TestbedConfig) -> Pnet:
    if not testbed.wifi.ssid:
        pytest.skip('requires wifi: set wifi.ssid / --wifi-ssid')
    if initialized.state.iface_down:
        initialized.iface_up()
    if not initialized.state.connected:
        initialized.connect(testbed.wifi.ssid, testbed.wifi.psk,
                            timeout=testbed.wifi.connect_timeout_s,
                            post_connect_delay_s=testbed.wifi.post_connect_delay_s)
    return initialized


@pytest.fixture(scope='session')
def peers(testbed: TestbedConfig):
    if testbed.peer.mode != 'local':
        yield None
        return
    pool = PeerPool(testbed.peer.bind, testbed.peer.tcp_base_port, testbed.peer.udp_base_port,
                    testbed.peer.max_sockets).start()
    yield pool
    pool.stop()


@pytest.fixture
def peer(peers, testbed):
    """Fresh peer state for the test. None when using an external peer."""
    if peers is not None:
        peers.reset()
    return peers


@pytest.fixture
def mqtt_host_client(testbed):
    from pnet_lib.mqtt_peer import MqttPeer
    client = MqttPeer(testbed.mqtt.host, testbed.mqtt.port)
    yield client
    client.close()


# ---------------------------------------------------------------------- guard / recovery
def _recover(pnet: Pnet) -> str:
    sh = pnet.sh
    if sh.resync():
        # The timed-out command may have left anything half-done; re-validate lazily.
        pnet.state.link_lost()
        return 'resynced'
    try:
        pnet.reset()
        return 'reset'
    except Exception as exc:
        pnet.board_dead = True
        return f'dead ({exc})'


@pytest.fixture(autouse=True)
def board_guard(request):
    if 'pnet' not in request.fixturenames:
        yield
        return
    pnet: Pnet = request.getfixturevalue('pnet')
    if pnet.board_dead:
        pytest.skip('Board unrecoverable after an earlier failure')
    if not pnet.sh.synchronized:
        logger.warning('Recovering board before test: %s', _recover(pnet))
    yield
    if not pnet.sh.synchronized:
        logger.warning('Recovering board after test: %s', _recover(pnet))
        return
    errors = []
    try:
        errors = pnet.cleanup()
    except SyncLostError:
        logger.warning('Recovering board during cleanup: %s', _recover(pnet))
    if errors:
        logger.warning('Cleanup issues: %s', errors)
    try:
        pnet.sh.check_fatal()
    except FatalLogError:
        pnet.state.rebooted()     # assume the worst; next test re-inits
        raise


@pytest.fixture
def tcp_addr(testbed):
    """tcp_addr(n) -> (peer_ip, tcp_base_port + n). Socket id n always uses port base + n."""
    def addr(n: int = 0) -> tuple[str, int]:
        return testbed.peer.ip, testbed.peer.tcp_base_port + n
    return addr


@pytest.fixture
def udp_addr(testbed):
    def addr(n: int = 0) -> tuple[str, int]:
        return testbed.peer.ip, testbed.peer.udp_base_port + n
    return addr
