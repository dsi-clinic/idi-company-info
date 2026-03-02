#!/usr/bin/env python3
"""
Pytest configuration file
"""

import sys
from pathlib import Path

# Add src directory to Python path
src_dir = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_dir))

# Add ftm2j repo root so archive.processing.orchestrator is importable
ftm2j_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ftm2j_root))
