"""BU - Smoke & bring-up."""
import pytest

from pnet_lib.client import Pnet

pytestmark = pytest.mark.smoke

EXPECTED_SUBCOMMANDS = {'init', 'fw_version', 'connect', 'resolve', 'ps', 'tcp_connect',
                        'tcp_tx', 'tcp_rx', 'tcp_disconnect', 'test_run_all'}


@pytest.mark.scope_id('BU-001')
def test_help_lists_subcommands(pnet: Pnet):
    names = set(pnet.subcommands())
    missing = EXPECTED_SUBCOMMANDS - names
    assert not missing, f'pnet help is missing {sorted(missing)}; got {sorted(names)}'


@pytest.mark.scope_id('BU-002')
def test_init(pnet: Pnet):
    if pnet.state.initialized:
        pytest.skip('Already initialised in this boot; BU-004 covers repeat init')
    pnet.init()


@pytest.mark.scope_id('BU-003')
def test_fw_version(initialized: Pnet, testbed, record_property):
    versions = initialized.fw_version()
    record_property('driver_version', versions['driver'])
    record_property('firmware_version', versions['firmware'])
    assert versions['firmware'], 'WiFi firmware version line missing'
    if testbed.expected_fw_version:
        assert testbed.expected_fw_version in versions['firmware'], versions


@pytest.mark.scope_id('BU-004')
def test_repeated_init(initialized: Pnet):
    res = initialized.run('pnet init', timeout=90)
    assert initialized.responsive()
    # Either succeeds again or reports a clean "already" style message; never a fault.
    initialized.sh.check_fatal()
    initialized.fw_version()
    assert res.text is not None


@pytest.mark.negative
@pytest.mark.scope_id('BU-005')
def test_commands_before_init(pnet: Pnet):
    pnet.reset()
    commands = (
        ('pnet connect BU-005-test', 'Connect request failed with ', 'Connect request sent'),
        ('pnet resolve example.com sync', 'DNS failed to resolve ', 'A: '),
        ('pnet tcp_connect 192.0.2.1 10001', 'Failed to connect socket', 'TCP connect successful'),
        ('pnet ps 1', 'WIFI_PS_PARAM_STATE failed', 'Done'),
    )
    failures = []
    for command, error, success in commands:
        # Returning from run() requires the shell to answer its next UART marker.
        res = pnet.run(command)
        pnet.sh.check_fatal()
        if not res.has(error) or any(line.strip().startswith(success) for line in res.lines):
            failures.append(f'Expected {error!r} and no {success!r}:\n{res}')

    pnet.init()
    assert pnet.fw_version()['firmware'], 'Wi-Fi firmware version missing after init'
    assert not failures, '\n'.join(failures)


@pytest.mark.scope_id('BU-008')
def test_interface_cycle_without_connection(initialized: Pnet):
    if initialized.state.connected:
        pytest.skip('Wi-Fi already connected in this boot (covered by WIFI-009)')
    initialized.iface_down()
    initialized.iface_up()
