"""Save pytest logs and test results by default."""

from tempfile import mkdtemp

import pytest

from os.path import expandvars
from pathlib import Path
from xml.etree import ElementTree

@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Set artifact paths before pytest configures logging and JUnit."""
    if config.option.collectonly:
        return

    logs_dir = config.rootpath / "logs"
    logs_dir.mkdir(exist_ok=True)
    run_dir = mkdtemp(prefix="run-", dir=logs_dir)

    if not config.option.log_file:
        config.option.log_file = f"{run_dir}/pytest.log"
    if not config.option.log_file_level:
        config.option.log_file_level = "DEBUG"
    if not config.option.xmlpath:
        config.option.xmlpath = f"{run_dir}/results.xml"

@pytest.hookimpl(wrapper=True)
def pytest_sessionfinish(session: pytest.Session):
    result = yield  # Let pytest write results.xml first.

    xml_path = session.config.getoption("xmlpath", default=None)
    if session.config.option.collectonly or not xml_path:
        return result

    report = Path(expandvars(xml_path)).expanduser()
    if not report.is_file():
        return result

    root = ElementTree.parse(report).getroot()
    suite = root.find("testsuite")
    if suite is None:
        return result

    counts = {key: int(suite.get(key, "0"))
              for key in ("tests", "failures", "skipped", "errors")}
    passed = counts["tests"] - counts["failures"] - counts["skipped"] - counts["errors"]
    lines = [
        f"{passed} passed | {counts['failures']} failed | "
        f"{counts['skipped']} skipped | {counts['errors']} errors",
        f"Duration: {suite.get('time', '?')} s",
        "",
    ]

    rows = []
    for case in suite.findall("testcase"):
        scope = case.find("properties/property[@name='scope_id']")
        scope_label = f"[{scope.get('value')}] " if scope is not None else ""
        status, reason = "PASS", ""
        for tag, label in (("error", "ERROR"), ("failure", "FAIL"), ("skipped", "SKIP")):
            detail = case.find(tag)
            if detail is not None:
                status = label
                message = detail.get("message", "").splitlines()
                reason = message[0] if message else ""
                break

        row = f"{status:<5} {scope_label}{case.get('name')} ({case.get('time', '?')} s)"
        if reason:
            row += f"\n      {reason}"
        rows.append((status == "PASS", row))

    lines.extend(row for _, row in sorted(rows, key=lambda entry: entry[0]))
    report.with_name("summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result