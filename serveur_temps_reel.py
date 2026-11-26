"""Serveur local de Mini Paris 3D avec le temps réel PRIM.

- sert la carte (index.html) sur http://localhost:8765
- interroge l'API PRIM « estimated-timetable » ligne par ligne, seulement quand la carte est ouverte
  (aucune requête si personne n'a appelé /api/temps-reel depuis 3 minutes)
- lit les perturbations du métro (API PRIM « disruptions_bulk ») toutes les 5 minutes, aux mêmes conditions

Économie du quota PRIM (1 000 requêtes par jour et par API, clé partagée avec le relais Cloudflare) :
un tour complet des 16 lignes toutes les 10 minutes par défaut, au plus 400 appels « par ligne »
par jour pour ce serveur, et 1 h de pause si PRIM répond 429 (quota atteint).
- expose le résultat simplifié sur /api/temps-reel (même format que le relais Cloudflare)

La clé PRIM est lue dans la variable d'environnement PRIM_API_KEY ou dans un fichier .env
(ligne PRIM_API_KEY=...). Ce fichier est exclu du dépôt par .gitignore.

Usage : python serveur_temps_reel.py [--port 8765] [--intervalle 600]
Bibliothèque standard uniquement, aucune installation nécessaire.
"""
import argparse, gzip, html, json, os, re, threading, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# Dossier du projet (le serveur sert les fichiers à partir d'ici)
ROOT = Path(__file__).resolve().parent
# API PRIM : prochains passages d'une ligne, et perturbations en cours
PRIM = 'https://prim.iledefrance-mobilites.fr/marketplace/estimated-timetable?LineRef=STIF:Line::{}:'
TRAFIC = 'https://prim.iledefrance-mobilites.fr/marketplace/disruptions_bulk/disruptions/v2'


# Lit la clé PRIM : variable d'environnement en priorité, sinon fichier .env à côté du script
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


# Correspondances produites par 04_extraire_horaires_gtfs.py
REFS = json.loads((ROOT / 'data/build/realtime_refs.json').read_text(encoding='utf-8'))
QUAYS, LINES = REFS['quays'], REFS['lines']   # n° de quai -> gare ; code ligne IDFM -> numéro de ligne

# État partagé renvoyé à la page : horaires par ligne, erreurs éventuelles, info trafic
# (protégé par un verrou, car il est lu et écrit par plusieurs fils d'exécution)
state = {'updated': 0, 'lines': {}, 'errors': {}, 'trafic': None}
lock = threading.Lock()
ACTIF = 180          # s : la carte est considérée ouverte si elle a appelé /api/temps-reel depuis moins de 3 min
PLAFOND_JOUR = 400   # appels PRIM « par ligne » au plus par jour pour ce serveur
PAUSE_429 = 3600     # s de pause après un refus de PRIM
# heure du dernier appel de la page à /api/temps-reel
derniere_demande = [0.0]
# appels PRIM « par ligne » du jour en cours
compteur = {'jour': '', 'n': 0}


# Vrai si une page a demandé le temps réel récemment : sinon, on n'interroge pas PRIM
def carte_ouverte():
    return time.time() - derniere_demande[0] < ACTIF


# Si PRIM répond 429 (quota du jour atteint), on arrête de l'interroger pendant une heure
def pause_si_quota(e):
    if isinstance(e, urllib.error.HTTPError) and e.code == 429:
        print('[PRIM] quota atteint (429) : pause d\'une heure')
        time.sleep(PAUSE_429)


# Convertit une heure ISO (« 2026-11-24T08:30:00Z ») en millisecondes depuis 1970
def epoch_ms(iso):
    return int(datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp() * 1000)


# Nom de la gare correspondant à un identifiant de quai PRIM
def quay_name(ref):
    # STIF:StopPoint:Q:463181: -> « Franklin D. Roosevelt »
    parts = (ref or '').strip(':').split(':')
    return QUAYS.get(parts[-1]) if parts else None


# Interroge PRIM pour une ligne et renvoie ses rames sous une forme simplifiée :
# [{id, dest, calls: [[gare, heure en ms], ...]}, ...]
def fetch_line(code, key):
    req = urllib.request.Request(PRIM.format(code), headers={'apikey': key, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    # réponse au format SIRI : une liste de « trajets de véhicule » avec leurs passages prévus
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
            # ne garde que les passages à venir (ou de moins d'une minute), dans l'ordre
            calls = sorted((c for c in calls if c[1] > now - 60_000), key=lambda c: c[1])
            if not calls:
                continue
            # destination : nom de la gare terminus, sinon le libellé fourni par PRIM
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


# Transforme le message HTML d'une perturbation en texte simple
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
    # heure de Paris au format des périodes IDFM (20261124T083000)
    maintenant = heure_paris().strftime('%Y%m%dT%H%M%S')
    # perturbations indexées par identifiant, et ordre de gravité pour le tri
    par_id = {d['id']: d for d in bulk['disruptions']}
    rang = {'BLOQUANTE': 0, 'PERTURBEE': 1, 'INFORMATION': 2}
    lines = {}
    for l in bulk.get('lines', []):
        ligne = LINES.get((l.get('id') or '').split(':')[-1])
        if l.get('mode') != 'Metro' or not ligne:
            continue
        # pour chaque perturbation de la ligne : gares touchées, ou toute la ligne
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
            # ne garde que les perturbations actives maintenant
            per = next((p for p in d.get('applicationPeriods', []) if p['begin'] <= maintenant <= p['end']), None)
            if per:
                out.append({'sev': d.get('severity'), 'cause': d.get('cause'), 'title': d.get('title'),
                            'msg': texte(d.get('message')), 'short': d.get('shortMessage') or '',
                            'stops': x['stops'], 'whole': x['whole'], 'end': per['end']})
        if out:
            lines[ligne] = sorted(out, key=lambda d: rang.get(d['sev'], 3))
    return {'updated': int(time.time() * 1000), 'lines': lines}


# Boucle de fond : relit l'info trafic toutes les 5 minutes, seulement si la carte est ouverte
def trafic_poller(key):
    while True:
        if not carte_ouverte():
            time.sleep(5)
            continue
        try:
            t = fetch_trafic(key)
            with lock:
                state['trafic'] = t
                state['errors'].pop('trafic', None)
        except (urllib.error.URLError, KeyError, ValueError, OSError) as e:
            with lock:
                state['errors']['trafic'] = str(e)[:200]
            print(f'[PRIM] info trafic : {e}')
            pause_si_quota(e)
        time.sleep(300)


# Boucle de fond : interroge les lignes une par une, en étalant les requêtes sur l'intervalle,
# seulement si la carte est ouverte, et sans dépasser le plafond du jour
def poller(key, interval):
    codes = list(LINES)
    pause = max(1.0, interval / len(codes))   # requêtes étalées sur l'intervalle
    essayees = set()
    while True:
        for code in codes:
            while not carte_ouverte():
                time.sleep(5)
            jour = heure_paris().strftime('%Y%m%d')
            if compteur['jour'] != jour:
                compteur.update(jour=jour, n=0)
            if compteur['n'] >= PLAFOND_JOUR:
                time.sleep(60)
                continue
            compteur['n'] += 1
            essayees.add(code)
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
                pause_si_quota(e)
            # premier tour rapide pour remplir la carte, puis requêtes étalées sur l'intervalle
            time.sleep(pause if len(essayees) >= len(codes) else 1)


# Serveur web : sert les fichiers du projet et la route /api/temps-reel
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def do_GET(self):
        if self.path.startswith('/api/temps-reel'):
            derniere_demande[0] = time.time()
            with lock:
                body = json.dumps(state, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return
        # ne jamais servir la clé PRIM ni les données brutes
        if self.path.startswith('/.env') or self.path.startswith('/data/sources'):
            self.send_error(404)
            return
        super().do_GET()

    def log_message(self, fmt, *args):
        pass  # silencieux


if __name__ == '__main__':
    # Options de lancement
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--intervalle', type=float, default=600, help='secondes pour un tour complet des 16 lignes')
    a = ap.parse_args()
    key = load_key()
    # sans clé, le serveur sert quand même la carte (horaires théoriques)
    if key:
        threading.Thread(target=poller, args=(key, a.intervalle), daemon=True).start()
        threading.Thread(target=trafic_poller, args=(key,), daemon=True).start()
    mode = f'temps réel PRIM, tour complet toutes les {a.intervalle / 60:.0f} min quand la carte est ouverte' if key else 'horaires théoriques'
    print(f'Mini Paris 3D : http://localhost:{a.port}  ({mode})')
    print('Laisse cette fenêtre ouverte. Ctrl+C pour arrêter.')
    # écoute uniquement sur la machine locale (127.0.0.1)
    ThreadingHTTPServer(('127.0.0.1', a.port), Handler).serve_forever()
