"""DNS - sync (Zephyr resolver) and async (via RA6W2)."""
import time

import pytest

from pnet_lib.client import Pnet

pytestmark = pytest.mark.functional
MODES = ['sync', 'async']


@pytest.mark.parametrize('mode', [pytest.param('sync', marks=pytest.mark.scope_id('DNS-001')),
                                  pytest.param('async', marks=pytest.mark.scope_id('DNS-002'))])
def test_resolve(online: Pnet, testbed, mode):
    assert online.resolve(testbed.dns.host, mode)


@pytest.mark.scope_id('DNS-003')
@pytest.mark.parametrize('mode', MODES)
def test_resolve_host_matrix(online: Pnet, testbed, mode):
    failed = [h for h in testbed.dns.extra_hosts if online.resolve_fails(h, mode)]
    assert not failed, f'{mode} resolve failed for {failed}'


@pytest.mark.scope_id('DNS-005')
@pytest.mark.parametrize('mode', ['sync', 'async', 'mixed'])
@pytest.mark.parametrize('count,interval_ms', [(3, 100), (20, 0), (20, 500)],
                         ids=['3x100ms', '20x0ms', '20x500ms'])
def test_dns_loop(online: Pnet, testbed, mode, count, interval_ms):
    result = online.test_dns_loop(testbed.dns.host, mode, count, interval_ms)
    assert result.passed, result.result


@pytest.mark.scope_id('DNS-006')
def test_back_to_back_async(online: Pnet, testbed):
    """Several async queries without waiting; every one must complete or fail cleanly."""
    start = online.sh.mark()
    sent = 0
    for _ in range(3):
        res = online.run(f'pnet resolve "{testbed.dns.host}" async')
        sent += 'DNS query sent' in res.text
    assert sent >= 1, 'No async query accepted'
    deadline = time.monotonic() + 30
    done = 0
    while time.monotonic() < deadline and done < sent:
        done = sum(1 for line in online.sh.lines_since(start)
                   if 'DNS resolve complete' in line or 'DNS resolve failed' in line)
        time.sleep(0.5)
    assert done >= sent, f'{sent} async queries accepted, only {done} completed'
    assert online.responsive()
    assert online.resolve(testbed.dns.host, 'async')


@pytest.mark.negative
@pytest.mark.scope_id('DNS-007')
@pytest.mark.parametrize('mode', MODES)
def test_resolve_with_interface_down(online: Pnet, testbed, mode):
    online.iface_down()
    assert online.resolve_fails(testbed.dns.host, mode, timeout=30)
    assert online.responsive()


@pytest.mark.power
@pytest.mark.scope_id('DNS-008')
@pytest.mark.parametrize('mode', MODES)
def test_resolve_with_power_save(online: Pnet, testbed, mode):
    online.ps(1)
    time.sleep(testbed.ps.idle_before_wake_s)
    assert online.resolve(testbed.dns.host, mode)


@pytest.mark.negative
@pytest.mark.scope_id('DNS-009')
@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('host', ['invalid.invalid', 'no-such-host-pnet-1234.com', 'a..b',
                                  'x' * 64 + '.com', '-bad-.com'],
                         ids=['nxdomain_tld', 'nxdomain', 'empty_label', 'label_gt63', 'hyphens'])
def test_resolve_invalid(online: Pnet, testbed, mode, host):
    assert online.resolve_fails(host, mode, timeout=40)
    assert online.resolve(testbed.dns.host, 'sync'), 'Valid resolve broken after invalid query'


@pytest.mark.scope_id('DNS-011')
@pytest.mark.parametrize('mode', MODES)
def test_resolve_latency(online: Pnet, testbed, mode, record_property):
    samples = []
    for _ in range(5):
        t = time.monotonic()
        online.resolve(testbed.dns.host, mode)
        samples.append(time.monotonic() - t)
    record_property(f'dns_{mode}_latency_s', [round(s, 3) for s in samples])
