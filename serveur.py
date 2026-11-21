"""Serveur local de Mini Paris 3D avec le temps réel PRIM.

- sert la carte (index.html) sur http://localhost:8765
- interroge l'API PRIM « estimated-timetable » ligne par ligne, en tâche de fond
- lit les perturbations du métro (API PRIM « disruptions_bulk ») toutes les 2 minutes
- expose le résultat simplifié sur /api/temps-reel (même format que le relais Cloudflare)

La clé PRIM est lue dans la variable d'environnement PRIM_API_KEY ou dans un fichier .env
(ligne PRIM_API_KEY=...). Ce fichier est exclu du dépôt par .gitignore.

Usage : python serveur.py [--port 8765] [--intervalle 60]
Bibliothèque standard uniquement, aucune installation nécessaire.
"""
import argparse, gzip, html, json, os, re, threading, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIM = 'https://prim.iledefrance-mobilites.fr/marketplace/estimated-timetable?LineRef=STIF:Line::{}:'
TRAFIC = 'https://prim.iledefrance-mobilites.fr/marketplace/disruptions_bulk/disruptions/v2'


def load_key():
    key = os.environ.get('PRIM_API_KEY', '').strip()
    env = ROOT / '.env'
    if not key and env.exists():
        for line in env.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('PRIM_API_KEY='):
                key = line.split('=', 1)[1].strip().strip('"\'')
    if not key:
        print("Clé PRIM absente : la carte tourne avec les horaires théoriques (ajoute PRIM_API_KEY=ta_cle dans .env pour le temps réel).")
    return key


REFS = json.loads((ROOT / 'data/build/realtime_refs.json').read_text(encoding='utf-8'))
QUAYS, LINES = REFS['quays'], REFS['lines']   # n° de quai -> gare ; code ligne IDFM -> numéro de ligne

state = {'updated': 0, 'lines': {}, 'errors': {}, 'trafic': None}
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


def heure_paris():
    """Heure de Paris sans dépendre de la base de fuseaux (absente du Python de Windows)."""
    now = datetime.now(timezone.utc)
    def dernier_dimanche(mois):
        d = datetime(now.year, mois, 31, 1, tzinfo=timezone.utc)
        return d - timedelta(days=(d.weekday() + 1) % 7)
    ete = dernier_dimanche(3) <= now < dernier_dimanche(10)
    return now + timedelta(hours=2 if ete else 1)


def texte(h):
    h = re.sub(r'<br\s*/?>|</p>', '\n', h or '', flags=re.I)
    h = html.unescape(re.sub(r'<[^>]+>', '', h))
    return re.sub(r'\n{3,}', '\n\n', h).strip()


def fetch_trafic(key):
    """Perturbations en cours sur les lignes de métro : { updated, lines: { "13": [...] } }."""
    req = urllib.request.Request(TRAFIC, headers={'apikey': key, 'Accept': 'application/json', 'Accept-Encoding': 'gzip'})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        if r.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
    bulk = json.loads(raw)
    maintenant = heure_paris().strftime('%Y%m%dT%H%M%S')
    par_id = {d['id']: d for d in bulk['disruptions']}
    rang = {'BLOQUANTE': 0, 'PERTURBEE': 1, 'INFORMATION': 2}
    lines = {}
    for l in bulk.get('lines', []):
        ligne = LINES.get((l.get('id') or '').split(':')[-1])
        if l.get('mode') != 'Metro' or not ligne:
            continue
        par_dis = {}
        for o in l.get('impactedObjects', []):
            for i in o.get('disruptionIds', []):
                x = par_dis.setdefault(i, {'stops': [], 'whole': False})
                if o.get('type') == 'line':
                    x['whole'] = True
                elif o.get('name') and o['name'] not in x['stops']:
                    x['stops'].append(o['name'])
        out = []
        for i, x in par_dis.items():
            d = par_id.get(i)
            if not d:
                continue
            per = next((p for p in d.get('applicationPeriods', []) if p['begin'] <= maintenant <= p['end']), None)
            if per:
                out.append({'sev': d.get('severity'), 'cause': d.get('cause'), 'title': d.get('title'),
                            'msg': texte(d.get('message')), 'short': d.get('shortMessage') or '',
                            'stops': x['stops'], 'whole': x['whole'], 'end': per['end']})
        if out:
            lines[ligne] = sorted(out, key=lambda d: rang.get(d['sev'], 3))
    return {'updated': int(time.time() * 1000), 'lines': lines}


def trafic_poller(key):
    while True:
        try:
            t = fetch_trafic(key)
            with lock:
                state['trafic'] = t
                state['errors'].pop('trafic', None)
        except (urllib.error.URLError, KeyError, ValueError, OSError) as e:
            with lock:
                state['errors']['trafic'] = str(e)[:200]
            print(f'[PRIM] info trafic : {e}')
        time.sleep(120)


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
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--intervalle', type=float, default=60, help='secondes pour un tour complet des 16 lignes')
    a = ap.parse_args()
    key = load_key()
    if key:
        threading.Thread(target=poller, args=(key, a.intervalle), daemon=True).start()
        threading.Thread(target=trafic_poller, args=(key,), daemon=True).start()
    mode = f'temps réel PRIM, tour complet toutes les {a.intervalle:.0f} s' if key else 'horaires théoriques'
    print(f'Mini Paris 3D : http://localhost:{a.port}  ({mode})')
    print('Laisse cette fenêtre ouverte. Ctrl+C pour arrêter.')
    ThreadingHTTPServer(('127.0.0.1', a.port), Handler).serve_forever()
