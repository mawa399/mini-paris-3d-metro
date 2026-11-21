"""Données d'analyse de la version 2 : fréquentation et accessibilité des stations.

- Fréquentation : validations aux portiques RATP (open data IDFM, 2e trimestre 2026)
  -> entrées médianes par jour (semaine, samedi, dimanche) et profil horaire par type de jour.
- Accessibilité : champ wheelchair_boarding du GTFS IDFM, ligne par ligne
  (1 = accessible en fauteuil, 2 = non accessible, 0 = inconnu).

Sortie : data/build/enrich.json, indexé par identifiant de zone de correspondance (id_zdc).
Usage : python scripts/06_enrich.py   (le GTFS doit être dans data/sources/IDFM-gtfs.zip)
"""
import collections, csv, datetime, io, json, statistics, urllib.request, zipfile
from pathlib import Path

IDFM = 'https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets'
VALID = 'validations-reseau-ferre-nombre-validations-par-jour-2eme-trimestre'
PROFIL = 'validations-reseau-ferre-profils-horaires-par-jour-type-2eme-trimestre'
RES_RATP = '110'  # code réseau des validations RATP (métro)
JOURS = {'JOHV': 'semaine', 'SAHV': 'samedi', 'DIJFP': 'dimanche'}


def get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'mini-paris-3d'})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


stations = json.load(open('data/sources/stations.json', encoding='utf-8'))
zdcs = {str(z) for *_, z in stations}

# --- Fréquentation : entrées par jour ---
print('validations par jour…')
rows = get_json(f'{IDFM}/{VALID}/exports/json?select=jour,id_zdc,code_stif_res,sum(nb_vald)%20as%20n'
                '&group_by=jour,id_zdc,code_stif_res')
per_day = collections.defaultdict(lambda: collections.defaultdict(float))
for x in rows:
    if x['code_stif_res'] == RES_RATP and x['id_zdc'] is not None:
        per_day[str(int(x['id_zdc']))][x['jour'][:10]] += x['n']


def mediane(z, jours):
    v = [n for d, n in per_day[z].items() if datetime.date.fromisoformat(d).weekday() in jours]
    return round(statistics.median(v)) if v else 0


# --- Fréquentation : profil horaire (part des entrées de la journée, heure par heure) ---
print('profils horaires…')
prof = get_json(f'{IDFM}/{PROFIL}/exports/json?select=id_zdc,code_stif_res,cat_jour,trnc_horr_60,pourcentage_validations')
profil = collections.defaultdict(lambda: {k: [0.0] * 24 for k in JOURS})
for x in prof:
    if x['code_stif_res'] != RES_RATP or x['id_zdc'] is None or x['cat_jour'] not in JOURS or x['trnc_horr_60'] == 'ND':
        continue
    h = int(x['trnc_horr_60'].split('H')[0])
    profil[str(int(x['id_zdc']))][x['cat_jour']][h] += x['pourcentage_validations'] or 0

# --- Accessibilité : GTFS, arrêts desservis par chaque ligne de métro ---
print('accessibilité (GTFS)…')
z = zipfile.ZipFile('data/sources/IDFM-gtfs.zip')
rd = lambda name: csv.DictReader(io.TextIOWrapper(z.open(name), 'utf-8-sig'))
routes = {r['route_id']: r['route_short_name'].lower() for r in rd('routes.txt') if r['route_type'] == '1'}
trips = {t['trip_id']: routes[t['route_id']] for t in rd('trips.txt') if t['route_id'] in routes}
pairs = set()
with z.open('stop_times.txt') as f:
    reader = csv.reader(io.TextIOWrapper(f, 'utf-8-sig'))
    head = next(reader)
    ti, si = head.index('trip_id'), head.index('stop_id')
    for row in reader:
        line = trips.get(row[ti])
        if line:
            pairs.add((line, row[si]))
stops = {s['stop_id']: s for s in rd('stops.txt')}
acces = collections.defaultdict(lambda: collections.defaultdict(set))
for line, sid in pairs:
    s = stops[sid]
    acces[s['parent_station'].split(':')[-1]][line].add(s['wheelchair_boarding'] or '0')


def niveau(vals):
    if vals == {'1'}:
        return 1          # accessible
    if '1' in vals:
        return 3          # accessible en partie (un quai ou une direction)
    if '2' in vals:
        return 2          # non accessible
    return 0              # inconnu


out = {}
for zdc in sorted(zdcs):
    e = {}
    if zdc in per_day:
        e['v'] = [mediane(zdc, range(5)), mediane(zdc, {5}), mediane(zdc, {6})]
    if zdc in profil:
        e['h'] = [[round(p * 10) for p in profil[zdc][k]] for k in JOURS]   # ‰ de la journée, par heure
    if zdc in acces:
        e['a'] = {line: niveau(v) for line, v in acces[zdc].items()}
    out[zdc] = e

Path('data/build').mkdir(exist_ok=True)
json.dump(out, open('data/build/enrich.json', 'w', encoding='utf-8'), separators=(',', ':'))
print(f"{sum('v' in e for e in out.values())} gares avec fréquentation, "
      f"{sum('a' in e for e in out.values())} avec accessibilité → data/build/enrich.json")
