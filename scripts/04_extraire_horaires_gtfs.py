"""Extrait du GTFS IDFM les horaires théoriques du métro pour trois types de jour.

Sortie data/build/schedule.json :
  shapes   : [[ [lon,lat], ... ], cum[] ]        tracés GTFS des trajets
  patterns : [line, shapeIdx, dir, headsign, [[stopName, along_m], ...]]
  profiles : [patternIdx, [arr_offset...], [dep_offset...]]   (secondes depuis le départ)
  days     : {weekday|saturday|sunday: [[profileIdx, start_s], ...]}
"""
import zipfile, io, csv, json, math, sys, datetime
from collections import defaultdict

# Ouvre l'archive GTFS sans la décompresser
ZIP = sys.argv[1] if len(sys.argv) > 1 else 'data/sources/IDFM-gtfs.zip'
z = zipfile.ZipFile(ZIP)
# Lit un fichier CSV de l'archive ligne par ligne (sous forme de dictionnaires)
def rows(name):
    with z.open(name) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, 'utf-8-sig'))

# Trois jours de référence (une semaine sans jour férié) : semaine, samedi, dimanche
DAYS = {'weekday': datetime.date(2026, 11, 24), 'saturday': datetime.date(2026, 11, 28), 'sunday': datetime.date(2026, 11, 29)}
WD = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']   # colonnes de calendar.txt

# Lignes de métro (route_type 1 = métro dans le GTFS)
routes = {r['route_id']: r['route_short_name'].lower() for r in rows('routes.txt') if r['route_type'] == '1'}
print('lignes', sorted(routes.values()))

# Trajets du métro : ligne, service (jours de circulation), sens, destination, tracé
trips = {}
for t in rows('trips.txt'):
    if t['route_id'] in routes:
        trips[t['trip_id']] = (routes[t['route_id']], t['service_id'], int(t['direction_id'] or 0), t['trip_headsign'], t['shape_id'])
print('trajets métro (toutes dates)', len(trips))

# Services actifs chaque jour de référence :
# calendar.txt donne les jours de la semaine, calendar_dates.txt les exceptions (ajouts / suppressions)
services = {s for _, s, *_ in trips.values()}
active = {k: set() for k in DAYS}
for c in rows('calendar.txt'):
    if c['service_id'] not in services: continue
    s, e = c['start_date'], c['end_date']
    for k, d in DAYS.items():
        ds = d.strftime('%Y%m%d')
        if s <= ds <= e and c[WD[d.weekday()]] == '1':
            active[k].add(c['service_id'])
for c in rows('calendar_dates.txt'):
    if c['service_id'] not in services: continue
    for k, d in DAYS.items():
        if c['date'] == d.strftime('%Y%m%d'):
            # exception_type 1 = service ajouté ce jour-là, 2 = service retiré
            (active[k].add if c['exception_type'] == '1' else active[k].discard)(c['service_id'])
# ne garde que les trajets qui circulent au moins un des trois jours
keep = {tid for tid, t in trips.items() if any(t[1] in active[k] for k in DAYS)}
print('trajets retenus', len(keep), {k: sum(1 for tid in keep if trips[tid][1] in v) for k, v in active.items()})

# Convertit « HH:MM:SS » en secondes (les heures peuvent dépasser 24 pour les trajets après minuit)
def secs(h):
    a, b, c = h.split(':'); return int(a) * 3600 + int(b) * 60 + int(c)
# Horaires de passage de chaque trajet retenu (fichier le plus volumineux du GTFS)
st = defaultdict(list)
n = 0
for r in rows('stop_times.txt'):
    n += 1
    if r['trip_id'] in keep:
        st[r['trip_id']].append((int(r['stop_sequence']), r['stop_id'], secs(r['arrival_time']), secs(r['departure_time'])))
print('lignes stop_times lues', n)

# Arrêts (nom et position) et tracés nécessaires
need_stops = {s[1] for v in st.values() for s in v}
stops, parents = {}, {}
for s in rows('stops.txt'):
    stops[s['stop_id']] = (s['stop_name'], float(s['stop_lon']), float(s['stop_lat']))
need_shapes = {trips[t][4] for t in keep}
shp = defaultdict(list)
for s in rows('shapes.txt'):
    if s['shape_id'] in need_shapes:
        shp[s['shape_id']].append((int(s['shape_pt_sequence']), float(s['shape_pt_lon']), float(s['shape_pt_lat'])))

# Distance en mètres entre deux points [lon, lat]
R = 6371008.8
def dist(a, b):
    la = math.radians((a[1] + b[1]) / 2)
    return math.hypot(math.radians(b[0] - a[0]) * math.cos(la) * R, math.radians(b[1] - a[1]) * R)

# Tracés : chaque tracé GTFS n'est stocké qu'une fois, avec ses distances cumulées
shape_idx, shapes = {}, []
def get_shape(sid):
    if sid in shape_idx: return shape_idx[sid]
    pts = [(x, y) for _, x, y in sorted(shp[sid])]
    # simplification légère : on enlève les points à moins de 4 m du précédent
    simp = [pts[0]]
    for p in pts[1:]:
        if dist(simp[-1], p) > 4: simp.append(p)
    cum = [0.0]
    for i in range(1, len(simp)): cum.append(cum[-1] + dist(simp[i - 1], simp[i]))
    shape_idx[sid] = len(shapes)
    shapes.append([[[round(x, 5), round(y, 5)] for x, y in simp], [round(c, 1) for c in cum]])
    return shape_idx[sid]

def project(shape, p, start):
    """distance le long du tracé du point le plus proche, en avançant depuis `start`"""
    pts, cum = shape
    best = (1e18, start)
    for i in range(len(pts) - 1):
        if cum[i + 1] < start - 50: continue   # on ne revient pas en arrière (tracés en boucle)
        ax, ay = pts[i]; bx, by = pts[i + 1]
        k = math.cos(math.radians(ay))
        vx, vy = (bx - ax) * k, by - ay
        wx, wy = (p[0] - ax) * k, p[1] - ay
        L2 = vx * vx + vy * vy
        t = 0 if L2 == 0 else max(0, min(1, (wx * vx + wy * vy) / L2))
        q = (ax + (bx - ax) * t, ay + (by - ay) * t)
        d = dist(q, p)
        if d < best[0]: best = (d, cum[i] + t * (cum[i + 1] - cum[i]))
    return best[1]

# Compression des horaires :
# - motif (pattern) = ligne + tracé + sens + suite d'arrêts, avec la position de chaque arrêt sur le tracé
# - profil = motif + temps de parcours relatifs (secondes depuis le départ), partagés par de nombreux trajets
# - pour chaque jour : liste de (profil, heure de départ)
pattern_idx, patterns = {}, []
profile_idx, profiles = {}, []
days = {k: [] for k in DAYS}
for tid, seq in st.items():
    seq.sort()   # arrêts dans l'ordre du trajet
    line, sid, d, head, shape_id = trips[tid]
    stop_ids = tuple(s[1] for s in seq)
    pkey = (line, shape_id, d, stop_ids)
    if pkey not in pattern_idx:
        # nouveau motif : on place chaque arrêt le long du tracé
        si = get_shape(shape_id)
        along, prev = [], 0.0
        for s in stop_ids:
            nm, x, y = stops[s]
            a = project(shapes[si], (x, y), prev)
            along.append([nm, round(a, 1)]); prev = a
        pattern_idx[pkey] = len(patterns)
        patterns.append([line, si, d, head, along])
    pi = pattern_idx[pkey]
    t0 = seq[0][2]   # heure de départ du trajet
    arr = tuple(s[2] - t0 for s in seq); dep = tuple(s[3] - t0 for s in seq)
    fkey = (pi, arr, dep)
    if fkey not in profile_idx:
        profile_idx[fkey] = len(profiles)
        profiles.append([pi, list(arr), list(dep)])
    for k in DAYS:
        if sid in active[k]:
            days[k].append([profile_idx[fkey], t0])
for k in days: days[k].sort(key=lambda x: x[1])   # départs triés par heure

out = {'shapes': shapes, 'patterns': patterns, 'profiles': profiles, 'days': days,
       'dates': {k: v.isoformat() for k, v in DAYS.items()}}
s = json.dumps(out, ensure_ascii=False, separators=(',', ':'))
open('data/build/schedule.json', 'w').write(s)
print('tracés', len(shapes), 'motifs', len(patterns), 'profils', len(profiles),
      {k: len(v) for k, v in days.items()}, 'Ko', len(s) // 1024)

# --- correspondance quai -> gare pour raccorder le temps réel PRIM (STIF:StopPoint:Q:<n>:)
quays = {}
for tid, seq in st.items():
    for _, sid, _, _ in seq:
        if sid.startswith('IDFM:') and sid[5:].isdigit():
            quays[sid[5:]] = stops[sid][0]   # n° de quai -> nom de la gare
# code de ligne IDFM (C01371…) -> numéro de ligne (1…)
lines = {r_id.split(':')[-1]: name for r_id, name in routes.items()}
json.dump({'quays': quays, 'lines': lines}, open('data/build/realtime_refs.json', 'w'), ensure_ascii=False, separators=(',', ':'))
print('quais métro :', len(quays))
