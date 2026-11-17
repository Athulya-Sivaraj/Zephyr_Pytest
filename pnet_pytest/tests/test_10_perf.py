"""PERF - zperf (DUT) against iperf 2 (host). Run with PS off."""
import subprocess
import time

import pytest

from pnet_lib.client import Pnet

pytestmark = [pytest.mark.perf, pytest.mark.requires('iperf')]


@pytest.fixture
def iperf_server(testbed):
    procs = []

    def start(proto: str) -> None:
        port = testbed.perf.udp_port if proto == 'udp' else testbed.perf.tcp_port
        cmd = [testbed.perf.iperf_path, '-s', '-p', str(port), '-i', '1']
        if proto == 'udp':
            cmd.append('-u')
        procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
        time.sleep(1)

    yield start
    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


@pytest.mark.parametrize('proto', [pytest.param('udp', marks=pytest.mark.scope_id('PERF-001')),
                                   pytest.param('tcp', marks=pytest.mark.scope_id('PERF-003'))])
def test_zperf_upload(online: Pnet, testbed, iperf_server, proto, record_property):
    online.ps(0)
    iperf_server(proto)
    port = testbed.perf.udp_port if proto == 'udp' else testbed.perf.tcp_port
    kbps = online.zperf_upload(proto, testbed.peer.ip, port, testbed.perf.duration_s)
    record_property(f'zperf_{proto}_upload_kbps', kbps)
    minimum = testbed.perf.min_udp_kbps if proto == 'udp' else testbed.perf.min_tcp_kbps
    if minimum:
        assert kbps >= minimum, f'{proto} upload {kbps:.0f} kbps < {minimum} kbps'


@pytest.mark.scope_id('PERF-004')
@pytest.mark.parametrize('size', [64, 512, 1024, 1400])
@pytest.mark.parametrize('rate', ['1M', '5M', '10M'])
def test_zperf_udp_sweep(online: Pnet, testbed, iperf_server, size, rate, record_property):
    online.ps(0)
    iperf_server('udp')
    kbps = online.zperf_upload('udp', testbed.peer.ip, testbed.perf.udp_port,
                               testbed.perf.duration_s, packet_size=size, rate=rate)
    record_property(f'udp_{size}B_{rate}_kbps', kbps)


@pytest.mark.power
@pytest.mark.scope_id('PERF-005')
def test_zperf_udp_with_power_save(online: Pnet, testbed, iperf_server, record_property):
    online.ps(1)
    iperf_server('udp')
    kbps = online.zperf_upload('udp', testbed.peer.ip, testbed.perf.udp_port, testbed.perf.duration_s)
    record_property('zperf_udp_upload_ps_kbps', kbps)
