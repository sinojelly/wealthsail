"""Root test configuration and fixtures."""

import os
import sys


def pytest_configure(config):
    """Isolate pytest temporary directories per process on Windows."""
    if sys.platform == "win32" and not config.option.basetemp:
        pid_dir = os.path.join(".tmp", "pytest", str(os.getpid()))
        os.makedirs(pid_dir, exist_ok=True)
        config.option.basetemp = pid_dir
