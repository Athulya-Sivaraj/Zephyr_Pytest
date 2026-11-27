"""Save pytest logs and test results by default."""

from tempfile import mkdtemp

import pytest


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
