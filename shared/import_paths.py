from __future__ import annotations

import sys
from pathlib import Path
import os


def bootstrap_feature_paths() -> None:
    root = Path(__file__).resolve().parent.parent
    feature_roots = [
        root / "Features",
    ]

    for path in feature_roots:
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.append(path_str)
            
            
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
