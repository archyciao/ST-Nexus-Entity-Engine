"""Compatibility CLI for the production extraction package."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from world_simulator_schema.extraction import legacy as implementation
def __getattr__(name):
    return getattr(implementation, name)

if __name__ == "__main__":
    raise SystemExit(implementation.main())
else:
    sys.modules[__name__] = implementation
