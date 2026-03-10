from __future__ import annotations

import os
import sys
from pathlib import Path


os.environ.setdefault("JWT_SECRET", "test-jwt-secret")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

