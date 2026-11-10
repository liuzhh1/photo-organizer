"""Use the existing Conda interpreter, with optional project-local AI packages."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
versioned = root / f'.vendor-py{sys.version_info.major}{sys.version_info.minor}'
vendor = versioned if versioned.is_dir() else root / '.vendor'
if vendor.is_dir():
    sys.path.insert(0, str(vendor))
