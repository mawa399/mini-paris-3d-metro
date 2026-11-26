"""Construit le fond de carte à partir des extraits OpenStreetMap.

Entrées : data/sources/osm_raw.json (plans d'eau, parcs, limites administratives)
          data/sources/osm_rivers_raw.json (axes de la Seine, de la Marne et des canaux ; bois)
Sortie  : data/build/basemap.json
"""
import json
from shapely.geometry import LineString, Polygon, box
from shapely.ops import polygonize, unary_union, linemerge

CLIP = box(2.18, 48.74, 2.50, 48.96)
R4 = lambda c: [(round(x, 4), round(y, 4)) for x, y in c]
rnd = lambda g: [[[round(x, 5), round(y, 5)] for x, y in ring.coords] for ring in [g.exterior, *g.interiors]]


def build(rings):
    closed = [Polygon(c).buffer(0) for c in rings if len(c) >= 4 and c[0] == c[-1]]
    lines = [LineString(c) for c in rings if len(c) >= 2 and not (len(c) >= 4 and c[0] == c[-1])]
    pg = list(polygonize(unary_union(lines))) if lines else []
    return unary_union(closed + [p.buffer(0) for p in pg])


def polygons(geom, min_area):
    gs = [geom] if geom.geom_type == 'Polygon' else list(getattr(geom, 'geoms', []))
    return [g for g in gs if g.geom_type == 'Polygon' and g.area > min_area]


osm = json.load(open('data/sources/osm_raw.json'))
riv = json.load(open('data/sources/osm_rivers_raw.json'))

# petits plans d'eau et parcs fermés (les grands fleuves sont tracés par leur axe, plus bas)
def area_layer(kind):
    outer = [R4(c) for k, n, r, c in osm if k == kind and r != 'inner']
    inner = [R4(c) for k, n, r, c in osm if k == kind and r == 'inner']
    g = build(outer)
    if inner: g = g.difference(build(inner))
    return polygons(g.intersection(CLIP).simplify(0.00003), 2e-7)

water = [g for g in area_layer('water') if g.area < 0.00008]
parks = area_layer('park')
for name in ['Bois de Boulogne', 'Bois de Vincennes']:
    g = build([R4(c) for k, n, r, c in riv if k == 'bois' and n == name and r != 'inner'])
    inner = [R4(c) for k, n, r, c in riv if k == 'bois' and n == name and r == 'inner']
    if inner: g = g.difference(build(inner))
    parks += polygons(g.simplify(0.00004), 1e-6)

def width(n):
    if n == 'La Seine': return 150
    if 'Seine' in n: return 60
    if n == 'La Marne': return 90
    if 'Bassin' in n: return 70
    return 26

groups = {}
for k, n, flag, c in riv:
    if k != 'river' or len(c) < 2: continue
    if flag in ('yes', 'culvert') or (flag.lstrip('-').isdigit() and int(flag) < 0): continue  # parties couvertes
    groups.setdefault(n, []).append(LineString(c))
rivers = []
for n, ls in groups.items():
    u = unary_union(ls)
    m = (linemerge(u) if u.geom_type == 'MultiLineString' else u).intersection(CLIP).simplify(0.00005)
    for g in (m.geoms if hasattr(m, 'geoms') else [m]):
        if g.geom_type == 'LineString' and g.length > 0.0008:
            rivers.append({'n': n, 'w': width(n), 'p': [[round(x, 5), round(y, 5)] for x, y in g.coords]})

adm = []
for k, n, r, c in osm:
    if k.startswith('adm'):
        ls = LineString(R4(c)).intersection(CLIP)
        for g in (ls.geoms if hasattr(ls, 'geoms') else [ls]):
            if g.geom_type == 'LineString' and not g.is_empty:
                adm.append([k, [[x, y] for x, y in g.coords]])

out = {'water': [rnd(g) for g in water], 'park': [rnd(g) for g in parks], 'rivers': rivers, 'adm': adm}
json.dump(out, open('data/build/basemap.json', 'w'), separators=(',', ':'))
print('plans d\'eau', len(water), '· parcs', len(parks), '· cours d\'eau', len(rivers), '· limites', len(adm))
