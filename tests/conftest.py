"""Shared fixtures for the Hanchu ESS BLE tests."""

from __future__ import annotations

import os
import sys

# Allow `import custom_components.hanchu_ess_ble...` from the repo root
# without an install step.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
