"""Regenerate the scientific tables and figures from processed monthly inputs."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wdcgg_pipeline.analysis import main
if __name__ == "__main__": main()
