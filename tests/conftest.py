import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (
    ROOT / "services" / "feature-engine" / "src",
    ROOT / "services" / "api" / "src",
    ROOT / "services" / "scheduler" / "src",
    ROOT / "libs" / "infusion-models" / "src",
    ROOT / "libs" / "infusion-common" / "src",
    ROOT / "libs" / "infusion-streams" / "src",
):
    sys.path.insert(0, str(source))
