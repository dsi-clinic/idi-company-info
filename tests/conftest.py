#!/usr/bin/env python3
"""
Pytest configuration file
"""

import sys
from pathlib import Path

# Add src directory to Python path
src_dir = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_dir))

# Add idi_company_info repo root so archive.processing.orchestrator is importable
idi_company_info_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(idi_company_info_root))
