"""tsntool — configure, run and analyze OMNeT++/INET TSN simulations.

Step 1 (this milestone) provides the foundation: locate the OMNeT++/INET
installation, enumerate the runnable configurations in an ``omnetpp.ini``,
launch a chosen TSN showcase configuration headless via the ``inet`` wrapper,
optionally exposing the built-in MCP server, and read back the produced
result files. A minimal PySide6 GUI ties these together.
"""

__version__ = "0.1.0"

from .environment import OmnetppEnv, locate_environment, EnvironmentError  # noqa: F401
from .inifile import IniConfig, parse_configs  # noqa: F401
