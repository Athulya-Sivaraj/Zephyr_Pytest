"""STR - high-iteration loops. Scale with --stress-cycles (default 200)."""
import time

import pytest

from pnet_lib.client import Pnet, token

pytestmark = pytest.mark.stress


@pytest.mark.scope_id('STR-001')
@pytest.mark.requires('tcp_peer')
def test_tcp_connect_disconnect_cycles(online: Pnet, peer, tcp_addr, testbed, record_property):
    durations = []
    for i in range(testbed.stress_cycles):
        t = time.monotonic()
        online.tcp_connect(*tcp_addr(0))
        online.tcp_disconnect()
        durations.append(time.monotonic() - t)
    first, last = durations[:10], durations[-10:]
    record_property('cycle_s_first10_avg', sum(first) / len(first))
    record_property('cycle_s_last10_avg', sum(last) / len(last))
    assert sum(last) / len(last) < 3 * sum(first) / len(first), 'Connect/disconnect latency drift'


@pytest.mark.scope_id('STR-003')
@pytest.mark.requires('tcp_peer')
def test_ps_toggle_under_traffic(online: Pnet, peer, tcp_addr, testbed):
    online.tcp_connect(*tcp_addr(0))
    ok = total = 0
    deadline = time.monotonic() + min(600, testbed.stress_cycles * 3)
    state = 1
    while time.monotonic() < deadline:
        online.ps(state)
        state ^= 1
        for _ in range(3):
            msg = token('t')
            total += 1
            try:
                online.tcp_tx(msg)
                online.tcp_rx(msg, timeout=15)
                ok += 1
            except AssertionError:
                pass
    assert total and ok / total >= 0.99, f'traffic success {ok}/{total}'


@pytest.mark.scope_id('STR-004')
def test_wifi_reconnect_cycles(online: Pnet, testbed, record_property):
    cycles = max(5, testbed.stress_cycles // 10)
    times = []
    for _ in range(cycles):
        online.iface_down()
        online.iface_up()
        t = time.monotonic()
        online.connect(testbed.wifi.ssid, testbed.wifi.psk, timeout=testbed.wifi.connect_timeout_s)
        times.append(time.monotonic() - t)
    record_property('connect_s', [round(x, 1) for x in times])


@pytest.mark.scope_id('STR-005')
def test_dns_loop_mixed_long(online: Pnet, testbed):
    result = online.test_dns_loop(testbed.dns.host, 'mixed', 1000, 0, timeout=1800)
    assert result.passed, result.result
