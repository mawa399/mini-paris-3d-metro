"""Génère relais-cloudflare/worker.js (relais PRIM en ligne) en y intégrant data/build/realtime_refs.json.

Usage : python scripts/06_build_worker.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
refs = json.loads((ROOT / 'data/build/realtime_refs.json').read_text(encoding='utf-8'))
src = (ROOT / 'relais-cloudflare/worker.template.js').read_text(encoding='utf-8')
out = src.replace('/*REFS*/null', json.dumps(refs, ensure_ascii=False, separators=(',', ':')))
(ROOT / 'relais-cloudflare/worker.js').write_text(out, encoding='utf-8')
print(f'relais-cloudflare/worker.js : {len(out) // 1024} Ko')
