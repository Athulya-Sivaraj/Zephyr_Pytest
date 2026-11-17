"""RUN - the firmware's built-in test runner (pnet test_run / test_run_all)."""
import pytest

from pnet_lib.client import Pnet

pytestmark = pytest.mark.fw_runner


def _case_args(case, tcp, testbed):
    host, port = tcp
    dns = f'"{testbed.dns.host}"'
    return {
        'TC-003': ([host, port, 3, 100], 'TC-TCP-LOOP', True),
        'TC-004': ([dns, 'sync', 3, 100], 'TC-DNS-LOOP', False),
        'TC-005': ([], 'TC-005', False),
        'TC-008': ([host, port, 'PING', 1500], 'TC-008', True),
        'TC-010': ([3, 300, 300], 'TC-PS-TOGGLE', False),
    }[case]


@pytest.mark.scope_id('RUN-001')
@pytest.mark.parametrize('case', ['TC-003', 'TC-004', 'TC-005', 'TC-008', 'TC-010'])
def test_builtin_case(online: Pnet, peer, tcp_addr, testbed, case):
    args, end_id, needs_tcp = _case_args(case, tcp_addr(0), testbed)
    if needs_tcp and not testbed.peer.ip:
        pytest.skip('needs a TCP peer')
    if case == 'TC-003':
        online.tcp_connect(*tcp_addr(0))
    res = online.test_run(case, *args)
    assert f'TEST_END,{end_id}' in res.text and 'result=PASS' in res.text, res


@pytest.mark.scope_id('RUN-002')
@pytest.mark.requires('tcp_peer')
def test_run_all_and_summary(online: Pnet, peer, tcp_addr, testbed):
    online.tcp_connect(*tcp_addr(0))
    res = online.test_run_all(*tcp_addr(0), testbed.dns.host, 30, 1000)
    assert 'TEST_SUITE_END' in res.text, res
    summary = online.test_summary()
    assert 'verdict=PASS' in summary.text, summary
