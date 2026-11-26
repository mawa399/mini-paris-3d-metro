"""Télécharge les données sources de Mini Paris 3D.

- Tracés et gares du métro : open data Île-de-France Mobilités (API Opendatasoft)
- Horaires théoriques : GTFS IDFM (archive d'environ 160 Mo, non versionnée)
- Fond de carte : OpenStreetMap via l'API Overpass (eau, bois, limites administratives)

Usage : python scripts/01_telecharger_donnees.py [--skip-gtfs] [--skip-osm]
"""
import json, sys, urllib.request, urllib.parse
from pathlib import Path

# Dossier où sont enregistrées les données brutes
SRC = Path('data/sources')
SRC.mkdir(parents=True, exist_ok=True)

# Adresses des services utilisés
IDFM = 'https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets'   # open data IDFM
GTFS_URL = ('https://data.iledefrance-mobilites.fr/explore/dataset/offre-horaires-tc-gtfs-idfm/'
            'files/a925e164271e4bca93433756d6a340d1/download/')                     # archive GTFS
OVERPASS = 'https://overpass-api.de/api/interpreter'                                 # requêtes OpenStreetMap
BBOX = '48.74,2.18,48.96,2.50'  # emprise étudiée (sud, ouest, nord, est) : Paris et la petite couronne


# Envoie une requête HTTP et renvoie la réponse JSON (GET, ou POST si `data` est fourni)
def get_json(url, data=None):
    req = urllib.request.Request(url, data=data, headers={'User-Agent': 'mini-paris-3d'})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


# Arrondit une coordonnée à 5 décimales (environ 1 m), pour alléger les fichiers
def r5(v):
    return round(v, 5)


# Tracés des lignes de métro (jeu IDFM « traces-du-reseau-ferre-idf »)
# -> data/sources/segs.json : [[ligne, couleur, [[ [lon, lat], ... ], ...]], ...]
def fetch_traces():
    where = urllib.parse.quote('mode="METRO"')   # on ne garde que le métro (pas le RER ni le tram)
    g = get_json(f'{IDFM}/traces-du-reseau-ferre-idf/exports/geojson?where={where}')
    segs = []
    for f in g['features']:
        geom = f['geometry']
        # un tronçon peut être une ligne simple ou multiple : on ramène tout à une liste de parties
        parts = geom['coordinates'] if geom['type'] == 'MultiLineString' else [geom['coordinates']]
        segs.append([f['properties']['indice_lig'], f['properties']['colourweb_hexa'],
                     [[[r5(x), r5(y)] for x, y in part] for part in parts]])
    json.dump(segs, open(SRC / 'segs.json', 'w'))
    print('tronçons de métro :', len(segs))


# Emplacement des gares (jeu IDFM « emplacement-des-gares-idf »)
# -> data/sources/stations.json : [[nom, ligne, lon, lat, identifiant de zone de correspondance], ...]
def fetch_stations():
    where = urllib.parse.quote('mode="METRO"')
    g = get_json(f'{IDFM}/emplacement-des-gares-idf/exports/json?where={where}')
    st = [[x['nom_gares'], x['indice_lig'], r5(x['geo_point_2d']['lon']), r5(x['geo_point_2d']['lat']), x['id_ref_zdc']]
          for x in g]
    json.dump(st, open(SRC / 'stations.json', 'w'), ensure_ascii=False)
    print('gares (par ligne) :', len(st))


# Archive GTFS complète d'IDFM (horaires théoriques de tout le réseau)
# -> data/sources/IDFM-gtfs.zip, téléchargée par blocs de 1 Mo pour ne pas saturer la mémoire
def fetch_gtfs():
    dest = SRC / 'IDFM-gtfs.zip'
    print('téléchargement du GTFS IDFM…')
    req = urllib.request.Request(GTFS_URL, headers={'User-Agent': 'mini-paris-3d'})
    with urllib.request.urlopen(req, timeout=900) as r, open(dest, 'wb') as out:
        while chunk := r.read(1 << 20):
            out.write(chunk)
    print('GTFS :', dest, dest.stat().st_size // (1 << 20), 'Mo')


# Envoie une requête au langage Overpass QL et renvoie les éléments OpenStreetMap
def overpass(query):
    return get_json(OVERPASS, data=urllib.parse.urlencode({'data': query}).encode())


# Convertit les éléments OSM (chemins et relations) en liste de contours :
# [type, nom, rôle (outer = contour extérieur, inner = trou), [[lon, lat], ...]]
def members(elements, kind_of):
    out = []
    for e in elements:
        tg = e.get('tags', {})
        kind = kind_of(tg)                         # type d'objet (eau, parc, limite…) déduit des attributs
        rd = lambda c: [r5(c['lon']), r5(c['lat'])]
        if e['type'] == 'way' and 'geometry' in e:
            # chemin simple : un seul contour
            out.append([kind, tg.get('name', ''), 'outer', [rd(c) for c in e['geometry']]])
        if e['type'] == 'relation':
            # relation (multipolygone) : chaque membre est un morceau de contour
            for m in e.get('members', []):
                if m['type'] == 'way' and 'geometry' in m:
                    out.append([kind, tg.get('name', ''), m.get('role') or 'outer', [rd(c) for c in m['geometry']]])
    return out


# Éléments du fond de carte depuis OpenStreetMap
def fetch_osm():
    # grands parcs et bois que l'on veut voir sur la carte
    parks = 'Bois de Boulogne|Bois de Vincennes|Parc de la Villette|Parc des Buttes-Chaumont|Jardin du Luxembourg|Parc Montsouris|Jardin des Tuileries|Champ de Mars|Parc de Saint-Cloud|Parc Georges-Valbon'

    # Requête 1 : plans d'eau, parcs et limites des communes (niveau 8) et arrondissements (niveau 9)
    q1 = f'''[out:json][timeout:180];(
 way["natural"="water"]["water"~"river|canal|lake|basin|pond"]({BBOX});
 relation["natural"="water"]["water"~"river|canal|lake"]({BBOX});
 way["waterway"="riverbank"]({BBOX});
 relation["leisure"="park"]["name"~"{parks}"]({BBOX});
 way["leisure"="park"]["name"~"{parks}"]({BBOX});
 relation["boundary"="administrative"]["admin_level"~"^(8|9)$"]({BBOX});
);out geom;'''
    j = overpass(q1)
    # classe chaque élément : limite administrative (adm8/adm9), parc ou eau
    kind = lambda tg: ('adm' + tg['admin_level']) if tg.get('boundary') == 'administrative' else 'park' if tg.get('leisure') == 'park' else 'water'
    json.dump(members(j['elements'], kind), open(SRC / 'osm_raw.json', 'w'), separators=(',', ':'))

    # Requête 2 : axes de la Seine, de la Marne et des canaux, et contours des bois de Boulogne et Vincennes
    q2 = f'''[out:json][timeout:180];(
 way["waterway"~"^(river|canal)$"]["name"~"Seine|Marne|Canal Saint-Martin|Canal de l'Ourcq|Canal Saint-Denis|Bassin de la Villette"]({BBOX});
 relation["name"~"^Bois de (Boulogne|Vincennes)$"];
);out geom;'''
    j = overpass(q2)
    out = []
    for e in j['elements']:
        tg = e.get('tags', {})
        rd = lambda c: [r5(c['lon']), r5(c['lat'])]
        if e['type'] == 'way' and 'geometry' in e:
            # cours d'eau : on garde l'info « tunnel / niveau » pour écarter ensuite les parties couvertes
            out.append(['river', tg.get('name', ''), tg.get('tunnel') or tg.get('layer') or '', [rd(c) for c in e['geometry']]])
        if e['type'] == 'relation':
            # bois : morceaux de contour de la relation
            for m in e.get('members', []):
                if m['type'] == 'way' and 'geometry' in m:
                    out.append(['bois', tg.get('name', ''), m.get('role') or 'outer', [rd(c) for c in m['geometry']]])
    json.dump(out, open(SRC / 'osm_rivers_raw.json', 'w'), separators=(',', ':'))
    print('OSM : éléments enregistrés')


if __name__ == '__main__':
    # tracés et gares sont toujours téléchargés ; GTFS et OSM peuvent être sautés s'ils sont déjà là
    fetch_traces()
    fetch_stations()
    if '--skip-gtfs' not in sys.argv:
        fetch_gtfs()
    if '--skip-osm' not in sys.argv:
        fetch_osm()
