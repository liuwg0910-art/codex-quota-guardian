"""GUI-only entry: no console and no dependency on the shell's current directory."""
import os
import sys
from pathlib import Path
root = Path(__file__).resolve().parent
os.chdir(root)
sys.path.insert(0, str(root))
(root / 'data').mkdir(exist_ok=True)
log = open(root / 'data' / 'desktop.log', 'a', encoding='utf-8', buffering=1)
sys.stdout = sys.stderr = log
from guardian.__main__ import main
main()
