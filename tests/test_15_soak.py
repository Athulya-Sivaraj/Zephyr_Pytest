"""SOAK - long runs. Opt in with `-m soak`; set --soak-duration-s (86400 for 24 h)."""
import re

import pytest

from pnet_lib.client import Pnet

pytestmark = [pytest.mark.soak, pytest.mark.requires('tcp_peer')]


@pytest.mark.scope_id('SOAK-001')
def test_ps_enabled_soak(online: Pnet, peer, tcp_addr, testbed, record_property):
    online.ps(1)
    res = online.test_soak(testbed.soak_duration_s, *tcp_addr(0), testbed.dns.host, 5000)
    for name in ('TC-SOAK-TCP', 'TC-SOAK-DNS'):
        m = re.search(rf'TEST_RESULT,{name},pass=(\d+),fail=(\d+)', res.text)
        assert m, f'{name} result missing\n{res}'
        record_property(name, f'pass={m.group(1)} fail={m.group(2)}')
        assert int(m.group(1)) > 0 and int(m.group(2)) == 0, m.group(0)
    assert 'TEST_END,TC-SOAK,result=PASS' in res.text, res
