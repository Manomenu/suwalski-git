"""suwgit's entry point — what the launchers in ~/.local/bin and the scheduled task run.

A plain script rather than `python -m suwgit`, so nothing has to be on PYTHONPATH and
no venv has to stay healthy: the package needs nothing beyond the standard library.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from suwgit.main import main  # the path above has to be in place first

sys.exit(main())
