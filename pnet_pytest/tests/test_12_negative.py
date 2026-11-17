"""NEG - invalid input and error paths. Pass = no success line, no fault, shell responsive."""
import pytest

from pnet_lib.client import Pnet

pytestmark = pytest.mark.negative

# Subcommands that need arguments; running them bare must only print usage/error.
# (Commands that are valid without args - init, reset, assert, fw_version, tcp_rx... - are
# deliberately excluded.)
NEEDS_ARGS = ['connect', 'connect_bssid', 'resolve', 'ps', 'ps_li', 'tcp_connect', 'tcp_tx',
              'tcp_connect_id', 'tcp_tx_id', 'tcp_rx_id', 'tcp_disconnect_id', 'tcp_tx_loop_id',
              'udp_connect', 'udp_tx', 'mqtt_connect', 'mqtt_subscribe', 'mqtt_publish',
              'test_run', 'test_dns_loop', 'test_tcp_loop', 'test_ps_toggle', 'test_soak',
              'test_run_all']
SUCCESS = ('successful', 'Done', 'result=PASS', 'Connect request sent', 'DNS query sent')


def assert_rejected(pnet: Pnet, command: str, timeout: float = 30) -> None:
    res = pnet.run(command, timeout)
    hit = [s for s in SUCCESS if s in res.text]
    assert not hit, f'Invalid command reported success ({hit})\n{res}'
    assert pnet.responsive(), f'Shell unresponsive after: {command}'
    pnet.sh.check_fatal()


@pytest.mark.scope_id('NEG-001')
@pytest.mark.parametrize('sub', NEEDS_ARGS)
def test_missing_arguments(initialized: Pnet, sub):
    if sub not in initialized.subcommands():
        pytest.skip(f'pnet {sub} not in this firmware')
    assert_rejected(initialized, f'pnet {sub}')


@pytest.mark.scope_id('NEG-002')
def test_overlong_argument(initialized: Pnet):
    assert_rejected(initialized, 'pnet tcp_connect ' + '9' * 300 + ' 1')


@pytest.mark.scope_id('NEG-003')
@pytest.mark.parametrize('args', ['not.an.ip 10001', '300.1.1.1 10001', '192.168.1.1 0',
                                  '192.168.1.1 65536', '192.168.1.1 port', '192.168.1.1'],
                         ids=['bad_ip', 'octet_gt255', 'port0', 'port65536', 'port_text', 'no_port'])
def test_invalid_tcp_connect(online: Pnet, args):
    assert_rejected(online, f'pnet tcp_connect {args}', timeout=60)


@pytest.mark.scope_id('NEG-004')
@pytest.mark.parametrize('cmd', ['ps -1', 'ps 2', 'ps abc', 'ps_li 0', 'ps_li -1',
                                 'ps_li 65535', 'ps_li abc'])
def test_invalid_ps_values(online: Pnet, cmd):
    res = online.run(f'pnet {cmd}')
    assert online.responsive(), res
    online.sh.check_fatal()
    online.ps(0)                                   # known-safe state still reachable


@pytest.mark.scope_id('NEG-005')
@pytest.mark.parametrize('cmd', ['udp_connect 1.2.3.4 0', 'udp_connect 1.2.3.4 abc',
                                 'mqtt_subscribe "" 0', 'mqtt_subscribe a/b 3',
                                 'mqtt_publish a/# x 0', 'mqtt_publish a/b x 3'])
def test_invalid_udp_mqtt(online: Pnet, cmd):
    assert_rejected(online, f'pnet {cmd}')


@pytest.mark.scope_id('NEG-006')
@pytest.mark.parametrize('cmd', ['pnet test_run TC-999', 'pnet no_such_command'])
def test_unknown(initialized: Pnet, cmd):
    assert_rejected(initialized, cmd)


@pytest.mark.scope_id('NEG-007')
@pytest.mark.requires('tcp_peer')
def test_data_commands_with_interface_down(online: Pnet, tcp_addr, udp_addr, testbed):
    online.iface_down()
    assert_rejected(online, f'pnet tcp_connect {tcp_addr(0)[0]} {tcp_addr(0)[1]}', 90)
    assert_rejected(online, f'pnet udp_connect {udp_addr(0)[0]} {udp_addr(0)[1]}', 90)
    if testbed.mqtt.host:
        assert_rejected(online, f'pnet mqtt_connect "{testbed.mqtt.host}" {testbed.mqtt.port} x', 120)
