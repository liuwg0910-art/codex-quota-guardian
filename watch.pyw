"""Login-started, console-free ChatGPT lifecycle watcher."""
import os
import sys
from pathlib import Path
root = Path(__file__).resolve().parent
os.chdir(root)
sys.path.insert(0, str(root))
(root / 'data').mkdir(exist_ok=True)
log = open(root / 'data' / 'autostart.log', 'a', encoding='utf-8', buffering=1)
sys.stdout = sys.stderr = log
from guardian.autostart import main
main()
