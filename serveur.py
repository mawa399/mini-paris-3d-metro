"""Serveur local de Mini Paris 3D avec le temps réel PRIM.

- sert la carte (index.html) sur http://localhost:8000
- interroge l'API PRIM « estimated-timetable » ligne par ligne, en tâche de fond
- expose le résultat simplifié sur /api/temps-reel

La clé PRIM est lue dans la variable d'environnement PRIM_API_KEY ou dans un fichier .env
(ligne PRIM_API_KEY=...). Ce fichier est exclu du dépôt par .gitignore.

Usage : python serveur.py [--port 8000] [--intervalle 60]
Bibliothèque standard uniquement, aucune installation nécessaire.
"""
import argparse, json, os, threading, time, urllib.request, urllib.error
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIM = 'https://prim.iledefrance-mobilites.fr/marketplace/estimated-timetable?LineRef=STIF:Line::{}:'


def load_key():
    key = os.environ.get('PRIM_API_KEY', '').strip()
    env = ROOT / '.env'
    if not key and env.exists():
        for line in env.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('PRIM_API_KEY='):
                key = line.split('=', 1)[1].strip().strip('"\'')
    if not key:
        raise SystemExit("Clé PRIM manquante : crée un fichier .env avec la ligne PRIM_API_KEY=ta_cle")
    return key


REFS = json.loads((ROOT / 'data/build/realtime_refs.json').read_text(encoding='utf-8'))
QUAYS, LINES = REFS['quays'], REFS['lines']   # n° de quai -> gare ; code ligne IDFM -> numéro de ligne

state = {'updated': 0, 'lines': {}, 'errors': {}}
lock = threading.Lock()


def epoch_ms(iso):
    return int(datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp() * 1000)


def quay_name(ref):
    # STIF:StopPoint:Q:463181: -> « Franklin D. Roosevelt »
    parts = (ref or '').strip(':').split(':')
    return QUAYS.get(parts[-1]) if parts else None


def fetch_line(code, key):
    req = urllib.request.Request(PRIM.format(code), headers={'apikey': key, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    frames = data['Siri']['ServiceDelivery']['EstimatedTimetableDelivery'][0].get('EstimatedJourneyVersionFrame', [])
    now = time.time() * 1000
    journeys = []
    for f in frames:
        for vj in f.get('EstimatedVehicleJourney', []):
            calls = []
            for c in vj.get('EstimatedCalls', {}).get('EstimatedCall', []):
                name = quay_name(c.get('StopPointRef', {}).get('value'))
                t = c.get('ExpectedArrivalTime') or c.get('ExpectedDepartureTime')
                if name and t:
                    calls.append([name, epoch_ms(t)])
            calls = sorted((c for c in calls if c[1] > now - 60_000), key=lambda c: c[1])
            if not calls:
                continue
            dest = quay_name(vj.get('DestinationRef', {}).get('value'))
            if not dest:
                names = vj.get('DestinationName') or [{}]
                dest = names[0].get('value', '')
            journeys.append({'id': vj.get('DatedVehicleJourneyRef', {}).get('value', ''), 'dest': dest, 'calls': calls})
    return journeys


def poller(key, interval):
    codes = list(LINES)
    pause = max(1.0, interval / len(codes))   # requêtes étalées sur l'intervalle
    while True:
        for code in codes:
            line = LINES[code]
            try:
                j = fetch_line(code, key)
                with lock:
                    state['lines'][line] = j
                    state['errors'].pop(line, None)
                    state['updated'] = int(time.time() * 1000)
            except (urllib.error.URLError, KeyError, ValueError) as e:
                with lock:
                    state['errors'][line] = str(e)[:200]
                print(f'[PRIM] ligne {line} : {e}')
            time.sleep(pause)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def do_GET(self):
        if self.path.startswith('/api/temps-reel'):
            with lock:
                body = json.dumps(state, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith('/.env') or self.path.startswith('/data/sources'):
            self.send_error(404)
            return
        super().do_GET()

    def log_message(self, fmt, *args):
        pass  # silencieux


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--intervalle', type=float, default=60, help='secondes pour un tour complet des 16 lignes')
    a = ap.parse_args()
    key = load_key()
    threading.Thread(target=poller, args=(key, a.intervalle), daemon=True).start()
    print(f'Mini Paris 3D : http://localhost:{a.port}  (temps réel PRIM, tour complet toutes les {a.intervalle:.0f} s)')
    ThreadingHTTPServer(('127.0.0.1', a.port), Handler).serve_forever()
