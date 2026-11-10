# lets the tests import the modules the same way the scripts do

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "operations"))
sys.path.insert(0, str(ROOT / "collector"))
