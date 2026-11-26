"""PERF - zperf (DUT) against iPerf 2 (host)."""
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from pnet_lib.client import Pnet

pytestmark = [pytest.mark.perf, pytest.mark.requires('iperf')]


@pytest.fixture
def iperf_server(testbed):
    procs = []

    def start(proto: str) -> None:
        port = testbed.perf.udp_port if proto == 'udp' else testbed.perf.tcp_port
        cmd = [testbed.perf.iperf_path, '-s', '-p', str(port)]
        if proto == 'udp':
            cmd.append('-u')

        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        procs.append(proc)
        time.sleep(1)
        assert proc.poll() is None, (
            f'iPerf 2 {proto} server exited on port {port}: exit code {proc.returncode}'
        )

    yield start

    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


@pytest.mark.parametrize(
    'proto',
    [
        pytest.param('udp', marks=pytest.mark.scope_id('PERF-001')),
        pytest.param('tcp', marks=pytest.mark.scope_id('PERF-003')),
    ],
)
def test_zperf_upload(online: Pnet, testbed, iperf_server, proto, record_property):
    online.ps(0)
    iperf_server(proto)
    port = testbed.perf.udp_port if proto == 'udp' else testbed.perf.tcp_port
    kbps = online.zperf_upload(proto, testbed.peer.ip, port, testbed.perf.duration_s)
    record_property(f'zperf_{proto}_upload_kbps', kbps)

    minimum = testbed.perf.min_udp_kbps if proto == 'udp' else testbed.perf.min_tcp_kbps
    if minimum:
        assert kbps >= minimum, f'{proto} upload {kbps:.0f} kbps < {minimum} kbps'


@pytest.mark.scope_id('PERF-002')
def test_zperf_udp_download(online: Pnet, testbed, dut, record_property):
    dut_ip = os.getenv('PNET_DUT_IP')
    if not dut_ip:
        pytest.skip('Set PNET_DUT_IP to the DUT IPv4 address')

    stop_cmd = os.getenv('PNET_ZPERF_UDP_STOP_CMD', '').strip()
    stop_regex = os.getenv('PNET_ZPERF_UDP_STOP_REGEX', '').strip()
    if not stop_cmd or not stop_regex:
        pytest.skip('Configure the verified DUT UDP stop command and success regex')
    re.compile(stop_regex)

    port = testbed.perf.udp_port
    duration = testbed.perf.duration_s
    rate = os.getenv('PNET_UDP_DOWNLOAD_RATE', '1M')

    online.ps(0)
    try:
        dut.write(f'zperf udp download {port}\n'.encode('ascii'))
        dut.readlines_until(regex=r'(?i)UDP server started\b', timeout=10)

        run = subprocess.run(
            [
                testbed.perf.iperf_path, '-c', dut_ip, '-u', '-p', str(port),
                '-t', str(duration), '-b', rate, '-f', 'k',
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=duration + 30,
        )
        assert run.returncode == 0, run.stdout

        _, marker, report = run.stdout.partition('Server Report:')
        assert marker, f'No UDP server report from DUT:\n{run.stdout}'
        match = re.search(
            r'(\d+(?:\.\d+)?)\s+Kbits/sec\s+'
            r'\d+(?:\.\d+)?\s+ms\s+(\d+)\s*/\s*(\d+)',
            report,
            flags=re.IGNORECASE,
        )
        assert match, f'Could not parse DUT UDP server report:\n{report}'

        kbps, lost, total = float(match[1]), int(match[2]), int(match[3])
        assert math.isfinite(kbps) and kbps > 0 and 0 <= lost < total, report
        record_property('zperf_udp_download_kbps', kbps)
        record_property('zperf_udp_download_loss_pct', 100 * lost / total)
        record_property('zperf_udp_download_offered_rate', rate)
    finally:
        dut.write(f'{stop_cmd}\n'.encode('ascii'))
        dut.readlines_until(regex=stop_regex, timeout=10)

@pytest.mark.scope_id('PERF-004')
@pytest.mark.parametrize('size', [64, 512, 1024, 1400])
@pytest.mark.parametrize('rate', ['1M', '5M', '10M'])
def test_zperf_udp_sweep(online: Pnet, testbed, iperf_server, size, rate, record_property):
    online.ps(0)
    iperf_server('udp')
    kbps = online.zperf_upload(
        'udp', testbed.peer.ip, testbed.perf.udp_port,
        testbed.perf.duration_s, packet_size=size, rate=rate,
    )
    record_property(f'udp_{size}B_{rate}_kbps', kbps)


@pytest.mark.power
@pytest.mark.scope_id('PERF-005')
def test_zperf_udp_with_power_save(online: Pnet, testbed, iperf_server, record_property):
    online.ps(1)
    try:
        iperf_server('udp')
        kbps = online.zperf_upload(
            'udp', testbed.peer.ip, testbed.perf.udp_port, testbed.perf.duration_s,
        )
        record_property('zperf_udp_upload_ps_kbps', kbps)
    finally:
        online.ps(0)


@pytest.mark.scope_id('PERF-006')
@pytest.mark.parametrize('proto', ['udp', 'tcp'])
def test_zperf_upload_regression(online: Pnet, testbed, iperf_server, proto, record_property):
    baseline_path = os.getenv('PNET_PERF_BASELINE')
    if not baseline_path:
        pytest.skip('Set PNET_PERF_BASELINE to the approved board/firmware baseline JSON')

    baseline = json.loads(Path(baseline_path).read_text(encoding='utf-8-sig'))
    duration = testbed.perf.duration_s
    assert baseline['duration_s'] == duration, 'Baseline duration does not match the run'

    reference = float(baseline['upload_kbps'][proto])
    max_drop_pct = float(baseline['max_drop_pct'])
    assert math.isfinite(reference) and reference > 0, 'Invalid baseline throughput'
    assert math.isfinite(max_drop_pct) and 0 <= max_drop_pct < 100, 'Invalid tolerance'

    online.ps(0)
    iperf_server(proto)
    port = testbed.perf.udp_port if proto == 'udp' else testbed.perf.tcp_port
    actual = online.zperf_upload(proto, testbed.peer.ip, port, duration)
    minimum = reference * (1 - max_drop_pct / 100)

    record_property(f'{proto}_upload_kbps', actual)
    record_property(f'{proto}_baseline_kbps', reference)
    record_property(f'{proto}_minimum_kbps', minimum)
    assert math.isfinite(actual) and actual >= minimum, (
        f'{proto} upload {actual:.0f} kbps < {minimum:.0f} kbps '
        f'(baseline {reference:.0f} kbps, allowed drop {max_drop_pct:g}%)'
    )
