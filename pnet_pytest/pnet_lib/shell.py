"""UART shell transport with marker synchronisation and fatal-log monitoring.

Every command is followed by `shell echo <marker>`; the Zephyr shell runs lines in
order, so the marker is printed only once the command has returned. Unlike a plain
`clear_buffer()`, lines that arrive between commands are drained and scanned, so an
assert or an unexpected reboot between two tests is never silently thrown away.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from collections import deque

from twister_harness.exceptions import TwisterHarnessTimeoutException

logger = logging.getLogger(__name__)

ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')

DEFAULT_FATAL_PATTERNS = (
    r'ASSERTION FAIL',
    r'ZEPHYR FATAL ERROR',
    r'>>> ZEPHYR FATAL',
    r'Kernel panic',
    r'HardFault|Hard Fault|UsageFault|BusFault|MemManage',
    r'Stack overflow',
    r'\*\*\*\s*Booting Zephyr',      # unexpected reboot (suppressed while expect_reboot)
)
BOOT_PATTERN = re.compile(r'\*\*\*\s*Booting Zephyr')


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub('', text).rstrip('\r\n')


class SyncLostError(RuntimeError):
    """The shell stopped answering markers; the board needs recovery."""


class FatalLogError(AssertionError):
    """A fatal pattern (assert, fault, unexpected reboot) appeared on the UART."""


class PnetShell:
    def __init__(self, dut, timeout: float = 60.0, fatal_patterns=DEFAULT_FATAL_PATTERNS,
                 history: int = 2000):
        self.dut = dut
        self.base_timeout = timeout
        self.synchronized = True
        self.expect_reboot = False
        self._fatal = [re.compile(p) for p in fatal_patterns]
        self.fatal_lines: list[str] = []
        self.history: deque[str] = deque(maxlen=history)
        self.total_lines = 0

    # ------------------------------------------------------------------ scanning
    def _scan(self, lines: list[str]) -> list[str]:
        clean = [strip_ansi(line) for line in lines]
        for line in clean:
            self.history.append(line)
            self.total_lines += 1
            for pattern in self._fatal:
                if pattern.search(line):
                    if self.expect_reboot and BOOT_PATTERN.search(line):
                        continue
                    logger.error('Fatal pattern on UART: %s', line)
                    self.fatal_lines.append(line)
        return clean

    def drain(self) -> list[str]:
        """Read and scan everything already buffered (non-blocking)."""
        try:
            return self._scan(self.dut.readlines(print_output=True))
        except Exception:      # adapter without readlines(): fall back to dropping
            self.dut.clear_buffer()
            return []

    def check_fatal(self) -> None:
        self.drain()
        if self.fatal_lines:
            lines, self.fatal_lines = self.fatal_lines, []
            raise FatalLogError('Fatal output on UART:\n' + '\n'.join(lines))

    # ------------------------------------------------------------------ commands
    def exec(self, command: str, timeout: float | None = None,
             print_output: bool = True) -> list[str]:
        """Run one shell command, return its output lines (without the marker)."""
        if not self.synchronized:
            raise SyncLostError('UART synchronisation was lost earlier; recover first')
        self.drain()
        marker = f'PNET{uuid.uuid4().hex[:12]}'
        logger.info('Running command: %s', command if print_output else command.split()[0:2])
        self.dut.write(f'{command}\nshell echo {marker}\n'.encode())
        # (?<!echo ) skips the echoed "shell echo PNET..." line if shell echo is on.
        try:
            lines = self.dut.readlines_until(regex=rf'(?<!echo ){marker}',
                                             timeout=timeout or self.base_timeout,
                                             print_output=print_output)
        except TwisterHarnessTimeoutException as exc:
            self.synchronized = False
            raise SyncLostError(f'No reply to {command.split()[:2]} within '
                                f'{timeout or self.base_timeout}s') from exc
        clean = self._scan(lines)
        return [line for line in clean if marker not in line]

    def wait_for(self, regex: str, timeout: float, print_output: bool = True) -> list[str]:
        """Block until a line matching `regex` arrives (for async events)."""
        try:
            return self._scan(self.dut.readlines_until(regex=regex, timeout=timeout,
                                                       print_output=print_output))
        except TwisterHarnessTimeoutException as exc:
            raise TimeoutError(f'{regex!r} not seen within {timeout}s') from exc

    def mark(self) -> int:
        """Position in the UART stream; pass to lines_since() later."""
        self.drain()
        return self.total_lines

    def lines_since(self, mark: int) -> list[str]:
        self.drain()
        n = min(self.total_lines - mark, len(self.history))
        return list(self.history)[-n:] if n > 0 else []

    # ------------------------------------------------------------------ setup / recovery
    def echo_off(self) -> None:
        self.exec('shell echo off')
        out = '\n'.join(self.exec('shell echo'))
        if 'Echo status: off' not in out:
            raise RuntimeError(f'Unable to disable shell echo:\n{out}')

    def resync(self, attempts: int = 3) -> bool:
        """Try to get the marker protocol working again without rebooting."""
        for _ in range(attempts):
            self.dut.write(b'\n')
            time.sleep(0.5)
            self.drain()
            self.synchronized = True
            try:
                self.exec('shell echo', timeout=5)
                return True
            except SyncLostError:
                continue
        return False

    def wait_reboot(self, timeout: float = 30.0, settle_s: float = 3.0) -> None:
        """Wait for 'Booting Zephyr' after a deliberate reset, then restore echo off."""
        self.expect_reboot = True
        try:
            self.wait_for(BOOT_PATTERN.pattern, timeout=timeout)
            time.sleep(settle_s)
            self.drain()
        finally:
            self.expect_reboot = False
        self.synchronized = True
        self.dut.write(b'shell echo off\n')
        time.sleep(1)
        self.drain()
        self.echo_off()
