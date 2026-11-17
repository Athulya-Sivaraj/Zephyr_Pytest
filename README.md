# pnet eRPC/NCP Wi-Fi pytest suite

Host: nRF54LM20 (Zephyr, `pnet` shell) · NCP: RA6W2 over eRPC. Runs on the Twister pytest
harness (`twister_harness`). Test ids (`@pytest.mark.scope_id("TCP-001")`) match the
**Test Scope** workbook (`pnet_automation_test_scope.xlsx`).

## Layout

```
conftest.py            fixtures: pnet → initialized → online, peers, board_guard, known issues, requires()
pytest.ini             markers, --dut-scope=session, logging, junit
testbed.example.yaml   bench config → copy to testbed.local.yaml (not committed)
known_issues.yaml      scope ids expected to fail on this FW → strict xfail
testcase.yaml.example  running the same suite through `west twister`
pnet_lib/
  config.py            YAML + CLI + env config (CLI > env > YAML > default)
  shell.py             UART transport: marker sync, fatal-log scan, recovery, reboot wait
  client.py            typed `Pnet` client: one method per pnet command, board state tracking
  servers.py           host TCP/UDP peers (port base+N per socket id; echo/silent/push/FIN/RST/refuse)
  mqtt_peer.py         host MQTT client (paho) to verify publishes / publish to the DUT
tests/
  test_01_bringup.py      BU    smoke
  test_02_wifi.py         WIFI  functional
  test_03_dns.py          DNS   functional
  test_04_tcp.py          TCP   functional
  test_05_multisocket.py  SOCK  functional (4 sockets)
  test_06_udp.py          UDP   functional
  test_07_mqtt.py         MQTT  functional
  test_08_powersave.py    PS    power
  test_10_perf.py         PERF  perf (zperf ↔ iperf 2)
  test_12_negative.py     NEG   negative
  test_13_recovery.py     REC   recovery
  test_14_stress.py       STR   stress
  test_15_soak.py         SOAK  soak
  test_16_fw_runner.py    RUN   firmware test_run / test_run_all
  test_99_disruptive.py   reset / host-reset / assert (runs last)
```

## What changed vs. the first sample file

| Sample | Now |
|---|---|
| One file, tests ordered by hand | One module per category + markers; order no longer matters |
| Session fixtures return a cached "online" shell | `initialized` / `online` re-check `pnet.state` before every test and redo init/connect when needed |
| `clear_buffer()` drops lines between commands | Lines are drained and scanned; `ASSERTION FAIL`, faults, unexpected `Booting Zephyr` fail the test that caused them |
| Marker regex also matches the echoed command | `(?<!echo )PNET…` only matches the real output line |
| Timeout → every later test skipped | `board_guard` resyncs the shell, else `pnet reset` + lazy re-init; only that test fails |
| Commented-out failing tests | `known_issues.yaml` → strict xfail; XPASS tells you FW fixed it |
| Commented-out "needs server" tests | Suite runs its own TCP/UDP peers; server-side receive is asserted |
| Operator pushes RX data by hand | `peer.tcp[n].push(...)` |
| env vars only (`PNET_SSID`...) | Testbed YAML + CLI (`--wifi-ssid`, `--peer-ip`, ... same names as the old suite) + env |
| `dns_host()` default was a pasted markdown link | `dns.host` (default `example.com`) |
| 1 s sleep after every test | Removed; remaining waits are config values |
| No cleanup between tests | After each test: close tracked sockets / UDP / MQTT, `ps 0`, `iface_up` |
| — | UART tail attached to failing tests; metrics via `record_property` in the junit XML |

## Setup (Windows CMD, Zephyr root)

```bat
set ZEPHYR_BASE=%CD%
set PYTHONPATH=%ZEPHYR_BASE%\scripts\pylib\pytest-twister-harness\src;%ZEPHYR_BASE%\scripts\pylib\twister;%PYTHONPATH%
pip install pyyaml paho-mqtt
copy <suite>\testbed.example.yaml <suite>\testbed.local.yaml   & rem edit it
set PNET_PSK=<psk>
west build -p always -b nrf54lm20dk/nrf54lm20a/cpuapp <pnet app> -d build_pnet
west flash -d build_pnet
```

Allow `python.exe` through Windows Firewall for the peer ports (TCP 10001-10004, UDP 10101-10104 by
default), or the DUT's connects to the local peer will time out.

## Running

```bat
cd <suite>
python -m pytest --twister-harness --device-type=hardware --device-serial=COM25 ^
  --platform=nrf54lm20dk/nrf54lm20a/cpuapp --build-dir=<path>\build_pnet ^
  --testbed=testbed.local.yaml -m smoke
```

Write `--testbed=FILE` with `=`. Written as two words, pytest treats the file path as a test path
before the option exists, and then cannot find `pytest.ini`.

Common selections:

| Goal | Add |
|---|---|
| CI per build | `-m smoke` |
| Nightly functional | `-m "not soak and not stress and not disruptive"` |
| One category | `tests/test_05_multisocket.py` or `-m power` |
| One scenario | `-k TCP-001` won't work (ids are markers); use the node: `tests/test_04_tcp.py::test_tcp_lifecycle` |
| Stress / soak | `-m stress --stress-cycles=1000`, `-m soak --soak-duration-s=86400` |
| Report | `--junitxml=results.xml` (metrics + scope_id in properties) |

Don't pass `-p twister_harness.plugin`; it autoloads (same note as the old handbook).

## Skips: `requires(...)`

A test is skipped with the reason when the testbed lacks something:
`wifi`, `tcp_peer`, `local_peer` (server-side control: push/close/RST), `bssid`, `open_network`,
`mqtt`, `mqtt_tls`, `host_mqtt` (paho installed), `iperf`, and any lab capability listed under
`capabilities:` (`ap_control`, `ppk2`, `ncp_reset`, `allow_assert`).

## Known issues

`known_issues.yaml` maps a scope id (optionally `ID[param]`) to a reason. Those tests run as
**strict xfail**: still FAIL → reported as xfail; start passing → XPASS fails the run so you
remove the entry. Use `strict: false` for flaky ones, `run: false` for ones that hang the board.
Keep one file per firmware line if needed (`--known-issues=known_issues_0.4.6.yaml`).

## Adding a test

```python
@pytest.mark.scope_id('TCP-012')              # id from the workbook
@pytest.mark.requires('local_peer')
def test_slow_reader(online, peer, tcp_addr):
    peer.tcp[0].set_mode('slow_reader')
    online.tcp_connect(*tcp_addr(0))
    ...
```

Use `online` / `initialized` / `pnet` for the state you need, the `Pnet` methods instead of raw
strings, and `token()` for unique payloads (payloads must be one shell token, `[A-Za-z0-9_.:-]`).
Anything you open via `Pnet` is closed automatically after the test.

## Verified so far

- The whole suite was run against a **simulated board** (fake DUT with real sockets to the local
  peers): 208 tests collect; flows, fixtures, cleanup, known-issue xfail, skips, timeout recovery,
  fatal-log detection, reset/re-init and soak parsing behave as intended.
- **Not yet run on hardware.** Check these on the first real run:
  - `pnet` help parsing (`subcommands()`) against the real help text
  - async DNS: A-record lines before or after `DNS resolve complete` (both are handled)
  - error wording for negative tests (they assert "no success line + shell alive", not a message)
  - `zperf` summary format (`parse_zperf_rate`)
  - that `DeviceAdapter.readlines()` exists in your twister_harness version (it falls back to
    `clear_buffer()` if not, losing fatal-scan between commands)

## Not implemented yet (by scope id)

Need lab equipment / fixtures: WIFI-002/005/007/008/010/011/012/013/016, DNS-010, REC-001/002/005/006/007,
SOAK-002, PWR-001..004, BU-007.
Need firmware support: MQTT-002/003/004/011, PS-014/015, CONC-002/003/007.
Straightforward next additions: BU-005, DNS-004, TCP-005/012/013, SOCK-008/009/011, UDP-004/005,
MQTT-006/008/012/016/019, PS-004/012, PERF-002/006, STR-002/006..009, SOAK-003..005, RUN-003,
CONC-004/005 (separate multi_threaded_app image).
