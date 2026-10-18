"""Télécharge les données sources de Mini Paris 3D.

- Tracés et gares du métro : open data Île-de-France Mobilités (API Opendatasoft)
- Horaires théoriques : GTFS IDFM (archive d'environ 160 Mo, non versionnée)
- Fond de carte : OpenStreetMap via l'API Overpass (eau, bois, limites administratives)

Usage : python scripts/01_fetch_sources.py [--skip-gtfs] [--skip-osm]
"""
import json, sys, urllib.request, urllib.parse
from pathlib import Path

SRC = Path('data/sources')
SRC.mkdir(parents=True, exist_ok=True)
IDFM = 'https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets'
GTFS_URL = ('https://data.iledefrance-mobilites.fr/explore/dataset/offre-horaires-tc-gtfs-idfm/'
            'files/a925e164271e4bca93433756d6a340d1/download/')
OVERPASS = 'https://overpass-api.de/api/interpreter'
BBOX = '48.74,2.18,48.96,2.50'  # sud, ouest, nord, est


def get_json(url, data=None):
    req = urllib.request.Request(url, data=data, headers={'User-Agent': 'mini-paris-3d'})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def r5(v):
    return round(v, 5)


def fetch_traces():
    where = urllib.parse.quote('mode="METRO"')
    g = get_json(f'{IDFM}/traces-du-reseau-ferre-idf/exports/geojson?where={where}')
    segs = []
    for f in g['features']:
        geom = f['geometry']
        parts = geom['coordinates'] if geom['type'] == 'MultiLineString' else [geom['coordinates']]
        segs.append([f['properties']['indice_lig'], f['properties']['colourweb_hexa'],
                     [[[r5(x), r5(y)] for x, y in part] for part in parts]])
    json.dump(segs, open(SRC / 'segs.json', 'w'))
    print('tronçons de métro :', len(segs))


def fetch_stations():
    where = urllib.parse.quote('mode="METRO"')
    g = get_json(f'{IDFM}/emplacement-des-gares-idf/exports/json?where={where}')
    st = [[x['nom_gares'], x['indice_lig'], r5(x['geo_point_2d']['lon']), r5(x['geo_point_2d']['lat']), x['id_ref_zdc']]
          for x in g]
    json.dump(st, open(SRC / 'stations.json', 'w'), ensure_ascii=False)
    print('gares (par ligne) :', len(st))


def fetch_gtfs():
    dest = SRC / 'IDFM-gtfs.zip'
    print('téléchargement du GTFS IDFM…')
    req = urllib.request.Request(GTFS_URL, headers={'User-Agent': 'mini-paris-3d'})
    with urllib.request.urlopen(req, timeout=900) as r, open(dest, 'wb') as out:
        while chunk := r.read(1 << 20):
            out.write(chunk)
    print('GTFS :', dest, dest.stat().st_size // (1 << 20), 'Mo')


def overpass(query):
    return get_json(OVERPASS, data=urllib.parse.urlencode({'data': query}).encode())


def members(elements, kind_of):
    out = []
    for e in elements:
        tg = e.get('tags', {})
        kind = kind_of(tg)
        rd = lambda c: [r5(c['lon']), r5(c['lat'])]
        if e['type'] == 'way' and 'geometry' in e:
            out.append([kind, tg.get('name', ''), 'outer', [rd(c) for c in e['geometry']]])
        if e['type'] == 'relation':
            for m in e.get('members', []):
                if m['type'] == 'way' and 'geometry' in m:
                    out.append([kind, tg.get('name', ''), m.get('role') or 'outer', [rd(c) for c in m['geometry']]])
    return out


def fetch_osm():
    parks = 'Bois de Boulogne|Bois de Vincennes|Parc de la Villette|Parc des Buttes-Chaumont|Jardin du Luxembourg|Parc Montsouris|Jardin des Tuileries|Champ de Mars|Parc de Saint-Cloud|Parc Georges-Valbon'
    q1 = f'''[out:json][timeout:180];(
 way["natural"="water"]["water"~"river|canal|lake|basin|pond"]({BBOX});
 relation["natural"="water"]["water"~"river|canal|lake"]({BBOX});
 way["waterway"="riverbank"]({BBOX});
 relation["leisure"="park"]["name"~"{parks}"]({BBOX});
 way["leisure"="park"]["name"~"{parks}"]({BBOX});
 relation["boundary"="administrative"]["admin_level"~"^(8|9)$"]({BBOX});
);out geom;'''
    j = overpass(q1)
    kind = lambda tg: ('adm' + tg['admin_level']) if tg.get('boundary') == 'administrative' else 'park' if tg.get('leisure') == 'park' else 'water'
    json.dump(members(j['elements'], kind), open(SRC / 'osm_raw.json', 'w'), separators=(',', ':'))

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
            out.append(['river', tg.get('name', ''), tg.get('tunnel') or tg.get('layer') or '', [rd(c) for c in e['geometry']]])
        if e['type'] == 'relation':
            for m in e.get('members', []):
                if m['type'] == 'way' and 'geometry' in m:
                    out.append(['bois', tg.get('name', ''), m.get('role') or 'outer', [rd(c) for c in m['geometry']]])
    json.dump(out, open(SRC / 'osm_rivers_raw.json', 'w'), separators=(',', ':'))
    print('OSM : éléments enregistrés')


if __name__ == '__main__':
    fetch_traces()
    fetch_stations()
    if '--skip-gtfs' not in sys.argv:
        fetch_gtfs()
    if '--skip-osm' not in sys.argv:
        fetch_osm()
