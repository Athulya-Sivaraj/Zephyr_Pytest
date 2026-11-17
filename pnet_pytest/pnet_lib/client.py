"""Typed wrapper around the `pnet` shell commands.

Tests talk to `Pnet` instead of building strings, so success strings live in one
place and board state (initialised / connected / PS / open sockets / MQTT) is tracked
for cleanup and recovery.

Success strings are taken from the current firmware output. If the firmware gains a
machine-readable result line (e.g. `PNET_RC,<cmd>,rc=<n>`), switch `expect()` to it.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .shell import PnetShell

IPV4 = r'\d{1,3}(?:\.\d{1,3}){3}'


def shell_argument(value: str) -> str:
    if any(ch in value for ch in ('"', '\\', '\n', '\r')):
        raise ValueError('Shell arguments must not contain quotes, backslashes, or newlines')
    return f'"{value}"'


PAYLOAD = re.compile(r'[A-Za-z0-9_.:\-]+')


def payload_arg(value: str) -> str:
    """tcp_tx/udp_tx/mqtt_publish take the payload as one bare shell token."""
    if not PAYLOAD.fullmatch(value):
        raise ValueError(f'Payload must match {PAYLOAD.pattern}: {value!r}')
    return value


class PnetCommandError(AssertionError):
    pass


@dataclass
class CmdResult:
    command: str
    lines: list[str]

    @property
    def text(self) -> str:
        return '\n'.join(self.lines)

    def has(self, needle: str) -> bool:
        return needle in self.text

    def search(self, regex: str):
        return re.search(regex, self.text)

    def __str__(self) -> str:
        return f'$ {self.command}\n{self.text}'


@dataclass
class BoardState:
    initialized: bool = False
    connected: bool = False
    ps_enabled: bool = False
    tcp_default_open: bool = False
    tcp_ids: set[int] = field(default_factory=set)
    udp_open: bool = False
    mqtt_connected: bool = False
    iface_down: bool = False

    def link_lost(self) -> None:
        self.connected = False
        self.tcp_default_open = False
        self.tcp_ids.clear()
        self.udp_open = False
        self.mqtt_connected = False

    def rebooted(self) -> None:
        self.link_lost()
        self.initialized = False
        self.ps_enabled = False
        self.iface_down = False


@dataclass
class LoopResult:
    passed: bool
    result: CmdResult
    fields: dict[str, str]


TEST_END = re.compile(r'TEST_END,(?P<id>[^,\s]+)(?P<rest>[^\r\n]*)')


def parse_test_end(result: CmdResult, test_id: str) -> LoopResult:
    for m in TEST_END.finditer(result.text):
        if m.group('id') == test_id:
            kv = dict(re.findall(r'(\w+)=([^,\s]+)', m.group('rest')))
            return LoopResult(kv.get('result') == 'PASS', result, kv)
    return LoopResult(False, result, {})


class Pnet:
    def __init__(self, shell: PnetShell, timeout: float = 60.0):
        self.sh = shell
        self.timeout = timeout
        self.state = BoardState()

    # ------------------------------------------------------------------ primitives
    def run(self, command: str, timeout: float | None = None,
            print_output: bool = True) -> CmdResult:
        return CmdResult(command, self.sh.exec(command, timeout or self.timeout, print_output))

    def expect(self, command: str, expected: str, timeout: float | None = None,
               regex: bool = False, print_output: bool = True) -> CmdResult:
        res = self.run(command, timeout, print_output)
        ok = res.search(expected) if regex else res.has(expected)
        if not ok:
            shown = res if print_output else f'$ {" ".join(command.split()[:2])} ... (output hidden)'
            raise PnetCommandError(f'Expected {expected!r}\n{shown}')
        return res

    def responsive(self) -> bool:
        try:
            self.sh.exec('shell echo', timeout=10)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ bring-up
    def help(self) -> CmdResult:
        return self.expect('pnet', 'pnet - pnet commands')

    def subcommands(self) -> list[str]:
        """Parse the subcommand names from `pnet` help output."""
        names = []
        for line in self.help().lines:
            m = re.match(r'\s{2,}([a-z][a-z0-9_]+)\s*(?::|\s{2,}|$)', line)
            if m:
                names.append(m.group(1))
        return names

    def init(self, timeout: float = 90) -> CmdResult:
        res = self.expect('pnet init', 'wifi initialize successful:', timeout)
        self.state.initialized = True
        return res

    def fw_version(self) -> dict[str, str]:
        res = self.expect('pnet fw_version', 'WiFi driver version:')
        out = {}
        for key, label in (('driver', 'WiFi driver version:'), ('firmware', 'WiFi firmware version:')):
            m = re.search(re.escape(label) + r'\s*(.+)', res.text)
            out[key] = m.group(1).strip() if m else ''
        return out

    def reset(self, timeout: float = 30) -> None:
        self.sh.expect_reboot = True
        self.sh.drain()
        self.sh.dut.write(b'pnet reset\n')
        self.state.rebooted()
        self.sh.wait_reboot(timeout=timeout)

    def iface_down(self) -> CmdResult:
        res = self.expect('pnet iface_down', 'net_if_down() returned 0', 90)
        self.state.link_lost()
        self.state.iface_down = True
        return res

    def iface_up(self) -> CmdResult:
        res = self.expect('pnet iface_up', 'net_if_up() returned 0', 90)
        self.state.iface_down = False
        return res

    # ------------------------------------------------------------------ Wi-Fi
    def connect(self, ssid: str, psk: str = '', bssid: str = '', timeout: float = 90,
                post_connect_delay_s: float = 0) -> CmdResult:
        """Connect and block until IP is applied. Credentials are never printed."""
        args = [shell_argument(ssid)]
        if psk:
            args.append(shell_argument(psk))
        if bssid:
            args.append(shell_argument(bssid))
        sub = 'connect_bssid' if bssid else 'connect'
        res = self.run(f'pnet {sub} {" ".join(args)}', 30, print_output=False)
        if 'Connect request sent' not in res.text:
            raise PnetCommandError(f'Wi-Fi connect request failed:\n{res.text}')
        if 'IP configuration applied to interface' not in res.text:
            try:
                res.lines += self.sh.wait_for(
                    r'IP configuration applied to interface|[Cc]onnection (?:request )?failed'
                    r'|[Dd]isconnected|[Aa]uth\w* fail',
                    timeout=timeout, print_output=False)
            except TimeoutError as exc:
                raise PnetCommandError(f'No IP within {timeout}s') from exc
            if 'IP configuration applied to interface' not in res.text:
                raise PnetCommandError(f'Wi-Fi connect failed:\n{res.lines[-1]}')
        self.state.connected = True
        if post_connect_delay_s:
            time.sleep(post_connect_delay_s)
        return res

    def try_connect(self, ssid: str, psk: str = '', bssid: str = '',
                    timeout: float = 45) -> bool:
        try:
            self.connect(ssid, psk, bssid, timeout=timeout)
            return True
        except PnetCommandError:
            return False

    # ------------------------------------------------------------------ DNS
    def resolve(self, host: str, mode: str = 'sync', timeout: float = 60) -> list[str]:
        """Return the A records; raises if none."""
        if mode not in ('sync', 'async'):
            raise ValueError(mode)
        res = self.run(f'pnet resolve {shell_argument(host)} {mode}', timeout)
        if mode == 'async':
            if 'DNS query sent' not in res.text:
                raise PnetCommandError(f'Async DNS not sent\n{res}')
            if not re.search(r'DNS resolve (?:complete|failed)', res.text):
                try:
                    res.lines += self.sh.wait_for(r'DNS resolve (?:complete|failed)', timeout)
                except TimeoutError as exc:
                    raise PnetCommandError(f'Async DNS did not complete in {timeout}s') from exc
            if 'DNS resolve complete' not in res.text:
                raise PnetCommandError(f'Async DNS failed\n{res}')
            if not re.search(rf'\bA:\s+{IPV4}', res.text):
                try:                    # address lines may follow the "complete" line
                    res.lines += self.sh.wait_for(rf'\bA:\s+{IPV4}', timeout=2)
                except TimeoutError:
                    pass
        addrs = re.findall(rf'\bA:\s+({IPV4})', res.text)
        if not addrs:
            raise PnetCommandError(f'No A record for {host} ({mode})\n{res}')
        return addrs

    def resolve_fails(self, host: str, mode: str = 'sync', timeout: float = 60) -> bool:
        try:
            self.resolve(host, mode, timeout)
            return False
        except PnetCommandError:
            return True

    def test_dns_loop(self, host: str, mode: str, count: int, interval_ms: int,
                      timeout: float = 300) -> LoopResult:
        res = self.run(f'pnet test_dns_loop {shell_argument(host)} {mode} {count} {interval_ms}', timeout)
        return parse_test_end(res, 'TC-DNS-LOOP')

    # ------------------------------------------------------------------ power save
    def ps(self, enable: bool | int) -> CmdResult:
        res = self.expect(f'pnet ps {int(enable)}', 'Done')
        self.state.ps_enabled = bool(int(enable))
        return res

    def psr(self) -> CmdResult:
        return self.expect('pnet psr', 'Wifi power save configs setup done')

    def ps_li(self, interval: int) -> CmdResult:
        return self.expect(f'pnet ps_li {interval}', 'Done')

    def test_ps_toggle(self, count: int, on_ms: int, off_ms: int, timeout: float = 300) -> LoopResult:
        res = self.run(f'pnet test_ps_toggle {count} {on_ms} {off_ms}', timeout)
        return parse_test_end(res, 'TC-PS-TOGGLE')

    # ------------------------------------------------------------------ TCP (default socket)
    def tcp_connect(self, host: str, port: int) -> CmdResult:
        res = self.expect(f'pnet tcp_connect {host} {port}', 'TCP connect successful')
        self.state.tcp_default_open = True
        return res

    def tcp_tx(self, payload: str) -> CmdResult:
        return self.expect(f'pnet tcp_tx {payload_arg(payload)}', 'TCP send successful')

    def tcp_rx(self, expected: str | None = None, timeout: float = 60) -> CmdResult:
        return self.expect('pnet tcp_rx', f'TCP recv: {expected}' if expected else 'TCP recv:', timeout)

    def tcp_disconnect(self) -> CmdResult:
        res = self.expect('pnet tcp_disconnect', 'TCP disconnect successful')
        self.state.tcp_default_open = False
        return res

    def test_tcp_loop(self, host: str, port: int, count: int, interval_ms: int, payload: str,
                      timeout: float = 600) -> LoopResult:
        res = self.run(f'pnet test_tcp_loop {host} {port} {count} {interval_ms} {payload_arg(payload)}', timeout)
        return parse_test_end(res, 'TC-TCP-LOOP')

    # ------------------------------------------------------------------ TCP (socket ids)
    def tcp_connect_id(self, sock_id: int, host: str, port: int) -> CmdResult:
        res = self.expect(f'pnet tcp_connect_id {sock_id} {host} {port}', 'TCP connect successful')
        self.state.tcp_ids.add(sock_id)
        return res

    def tcp_tx_id(self, sock_id: int, payload: str) -> CmdResult:
        return self.expect(f'pnet tcp_tx_id {sock_id} {payload_arg(payload)}', 'TCP send successful')

    def tcp_rx_id(self, sock_id: int, expected: str | None = None, timeout: float = 60) -> CmdResult:
        return self.expect(f'pnet tcp_rx_id {sock_id}',
                           f'TCP recv: {expected}' if expected else 'TCP recv:', timeout)

    def tcp_disconnect_id(self, sock_id: int) -> CmdResult:
        res = self.expect(f'pnet tcp_disconnect_id {sock_id}', 'TCP disconnect successful')
        self.state.tcp_ids.discard(sock_id)
        return res

    def tcp_tx_loop_id(self, sock_id: int, count: int, interval_ms: int, payload: str,
                       timeout: float = 300) -> LoopResult:
        res = self.run(f'pnet tcp_tx_loop_id {sock_id} {count} {interval_ms} {payload_arg(payload)}', timeout)
        lr = parse_test_end(res, 'TC-TCP-TX-LOOP-ID')
        if lr.fields.get('id') not in (None, str(sock_id)):
            lr.passed = False
        return lr

    # ------------------------------------------------------------------ UDP
    def udp_connect(self, host: str, port: int) -> CmdResult:
        res = self.expect(f'pnet udp_connect {host} {port}', 'UDP connect successful')
        self.state.udp_open = True
        return res

    def udp_tx(self, payload: str) -> CmdResult:
        return self.expect(f'pnet udp_tx {payload_arg(payload)}', 'UDP send successful')

    def udp_rx(self, expected: str | None = None, timeout_ms: int = 10000) -> CmdResult:
        return self.expect(f'pnet udp_rx {timeout_ms}', f'data: {expected}' if expected else 'data:',
                           timeout_ms / 1000 + 10)

    def udp_disconnect(self) -> CmdResult:
        res = self.expect('pnet udp_disconnect', 'UDP disconnect successful')
        self.state.udp_open = False
        return res

    # ------------------------------------------------------------------ MQTT
    def mqtt_connect(self, host: str, port: int, client_id: str, timeout: float = 120) -> CmdResult:
        res = self.expect(f'pnet mqtt_connect {shell_argument(host)} {port} {client_id}',
                          'MQTT connect successful:', timeout)
        self.state.mqtt_connected = True
        return res

    def mqtt_subscribe(self, topic: str, qos: int = 0) -> CmdResult:
        return self.expect(f'pnet mqtt_subscribe {shell_argument(topic)} {qos}',
                           'MQTT subscribe successful:', 90)

    def mqtt_publish(self, topic: str, payload: str, qos: int = 0) -> CmdResult:
        return self.expect(f'pnet mqtt_publish {shell_argument(topic)} {payload_arg(payload)} {qos}',
                           'MQTT publish successful:', 90)

    def mqtt_disconnect(self) -> CmdResult:
        res = self.expect('pnet mqtt_disconnect', 'MQTT disconnect successful', 90)
        self.state.mqtt_connected = False
        return res

    # ------------------------------------------------------------------ FW built-in runner
    def test_run(self, case: str, *args, timeout: float = 600) -> CmdResult:
        return self.run(' '.join(['pnet test_run', case, *map(str, args)]), timeout)

    def test_run_all(self, host: str, port: int, dns_host: str, duration_s: int, interval_ms: int,
                     timeout: float = 900) -> CmdResult:
        return self.run(f'pnet test_run_all {host} {port} {shell_argument(dns_host)} '
                        f'{duration_s} {interval_ms}', timeout)

    def test_summary(self) -> CmdResult:
        return self.expect('pnet test_summary', 'TEST_SUITE_SUMMARY,')

    def test_soak(self, duration_s: int, host: str, port: int, dns_host: str, interval_ms: int) -> CmdResult:
        return self.run(f'pnet test_soak {duration_s} {host} {port} {shell_argument(dns_host)} '
                        f'{interval_ms}', timeout=duration_s + 300)

    # ------------------------------------------------------------------ zperf (Zephyr shell)
    def zperf_upload(self, proto: str, host: str, port: int, duration_s: int,
                     packet_size: int | None = None, rate: str | None = None) -> float:
        """Run `zperf <udp|tcp> upload` and return the reported rate in kbps."""
        cmd = f'zperf {proto} upload {host} {port} {duration_s}'
        if packet_size:
            cmd += f' {packet_size}'
            if rate and proto == 'udp':
                cmd += f' {rate}'
        res = self.run(cmd, timeout=duration_s + 60)
        if 'completed' not in res.text.lower():
            raise PnetCommandError(f'zperf upload did not complete\n{res}')
        return parse_zperf_rate(res.text)

    # ------------------------------------------------------------------ cleanup
    def cleanup(self) -> list[str]:
        """Best-effort return to 'connected, PS off, nothing open'. Returns errors."""
        errors = []
        steps = []
        if self.state.mqtt_connected:
            steps.append(self.mqtt_disconnect)
        for sid in sorted(self.state.tcp_ids):
            steps.append(lambda sid=sid: self.tcp_disconnect_id(sid))
        if self.state.tcp_default_open:
            steps.append(self.tcp_disconnect)
        if self.state.udp_open:
            steps.append(self.udp_disconnect)
        if self.state.ps_enabled and self.state.initialized:
            steps.append(lambda: self.ps(0))
        if self.state.iface_down:
            steps.append(self.iface_up)
        for step in steps:
            try:
                step()
            except Exception as exc:     # keep going; report everything
                errors.append(f'{getattr(step, "__name__", "step")}: {exc}')
        # Even if a close failed, forget the handle so the next test starts clean.
        self.state.mqtt_connected = self.state.tcp_default_open = self.state.udp_open = False
        self.state.tcp_ids.clear()
        return errors


def parse_zperf_rate(text: str) -> float:
    """Last 'rate: <n> <K|M>bps' in zperf output, in kbps."""
    matches = re.findall(r'[Rr]ate:\s*([\d.]+)\s*([KkMmGg]?)bps', text)
    if not matches:
        raise PnetCommandError(f'No rate in zperf output\n{text}')
    value, unit = matches[-1]
    scale = {'': 1 / 1000, 'k': 1, 'm': 1000, 'g': 1_000_000}[unit.lower()]
    return float(value) * scale


def token(tag: str, size: int = 0) -> str:
    """Unique, shell-safe payload: '<tag>-<hex>' padded with 'x' to `size` chars."""
    import uuid
    base = f'{tag}-{uuid.uuid4().hex[:8]}'
    return base if size <= len(base) else base + 'x' * (size - len(base))
