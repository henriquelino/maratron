#!/usr/bin/env python
"""Launcher so you can run the dashboard without setting PYTHONPATH.

    python python/run.py --mock          # from the repo root
    python run.py --mock                 # from inside python/

Puts python/src on sys.path, then hands off to maratron.app.main().
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from maratron.app import main  # noqa: E402

if __name__ == "__main__":
    main()
