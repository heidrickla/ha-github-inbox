"""Put the repository root on sys.path so `custom_components.github_inbox` imports in tests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
