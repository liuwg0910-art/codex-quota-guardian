import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guardian.lifecycle import refresh

if __name__ == '__main__':
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}
    result = refresh(payload)
    if not result['published']:
        print(json.dumps({'systemMessage': '额度守卫连接刷新失败，请检查 data/lifecycle-health.json。'}, ensure_ascii=False))
