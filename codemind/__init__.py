"""CodeMind — autonomous local coding AI with a native desktop application.

The package is deliberately dependency-light: everything except the Qt GUI binds to the
Python standard library, so the agent, the sandbox, the training pipeline and the storage
layer can run headless on a machine with no desktop session and no third-party wheels.
"""

from codemind.version import APP_NAME, APP_VERSION, BUILD_INFO

__all__ = ["APP_NAME", "APP_VERSION", "BUILD_INFO"]
