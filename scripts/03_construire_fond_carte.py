"""Construit le fond de carte à partir des extraits OpenStreetMap.

Entrées : data/sources/osm_raw.json (plans d'eau, parcs, limites administratives)
          data/sources/osm_rivers_raw.json (axes de la Seine, de la Marne et des canaux ; bois)
Sortie  : data/build/basemap.json
"""
import json
from shapely.geometry import LineString, Polygon, box
from shapely.ops import polygonize, unary_union, linemerge

# Emprise de la carte : tout ce qui dépasse est découpé
CLIP = box(2.18, 48.74, 2.50, 48.96)
# Arrondis des coordonnées : 4 décimales (~10 m) pour les calculs, 5 (~1 m) pour la sortie
R4 = lambda c: [(round(x, 4), round(y, 4)) for x, y in c]
rnd = lambda g: [[[round(x, 5), round(y, 5)] for x, y in ring.coords] for ring in [g.exterior, *g.interiors]]


# Reconstitue des surfaces à partir de contours OSM :
# les contours fermés deviennent directement des polygones,
# les morceaux ouverts sont recollés entre eux pour former des polygones.
def build(rings):
    closed = [Polygon(c).buffer(0) for c in rings if len(c) >= 4 and c[0] == c[-1]]
    lines = [LineString(c) for c in rings if len(c) >= 2 and not (len(c) >= 4 and c[0] == c[-1])]
    pg = list(polygonize(unary_union(lines))) if lines else []
    return unary_union(closed + [p.buffer(0) for p in pg])


# Éclate une géométrie en polygones simples et écarte les trop petits
def polygons(geom, min_area):
    gs = [geom] if geom.geom_type == 'Polygon' else list(getattr(geom, 'geoms', []))
    return [g for g in gs if g.geom_type == 'Polygon' and g.area > min_area]


osm = json.load(open('data/sources/osm_raw.json'))
riv = json.load(open('data/sources/osm_rivers_raw.json'))

# petits plans d'eau et parcs fermés (les grands fleuves sont tracés par leur axe, plus bas)
def area_layer(kind):
    outer = [R4(c) for k, n, r, c in osm if k == kind and r != 'inner']   # contours extérieurs
    inner = [R4(c) for k, n, r, c in osm if k == kind and r == 'inner']   # trous (îles, clairières…)
    g = build(outer)
    if inner: g = g.difference(build(inner))
    # découpe à l'emprise, simplifie légèrement et retire les micro-surfaces
    return polygons(g.intersection(CLIP).simplify(0.00003), 2e-7)

water = [g for g in area_layer('water') if g.area < 0.00008]   # seulement les petits plans d'eau
parks = area_layer('park')
# ajoute les bois de Boulogne et de Vincennes, extraits à part
for name in ['Bois de Boulogne', 'Bois de Vincennes']:
    g = build([R4(c) for k, n, r, c in riv if k == 'bois' and n == name and r != 'inner'])
    inner = [R4(c) for k, n, r, c in riv if k == 'bois' and n == name and r == 'inner']
    if inner: g = g.difference(build(inner))
    parks += polygons(g.simplify(0.00004), 1e-6)

# Largeur d'affichage (en mètres) de chaque cours d'eau, selon son nom
def width(n):
    if n == 'La Seine': return 150
    if 'Seine' in n: return 60      # bras de Seine
    if n == 'La Marne': return 90
    if 'Bassin' in n: return 70
    return 26                       # canaux

# Regroupe les morceaux de chaque cours d'eau par nom
groups = {}
for k, n, flag, c in riv:
    if k != 'river' or len(c) < 2: continue
    if flag in ('yes', 'culvert') or (flag.lstrip('-').isdigit() and int(flag) < 0): continue  # parties couvertes
    groups.setdefault(n, []).append(LineString(c))
# Fusionne les morceaux en lignes continues, découpe à l'emprise et simplifie
rivers = []
for n, ls in groups.items():
    u = unary_union(ls)
    m = (linemerge(u) if u.geom_type == 'MultiLineString' else u).intersection(CLIP).simplify(0.00005)
    for g in (m.geoms if hasattr(m, 'geoms') else [m]):
        if g.geom_type == 'LineString' and g.length > 0.0008:   # ignore les tout petits bouts
            rivers.append({'n': n, 'w': width(n), 'p': [[round(x, 5), round(y, 5)] for x, y in g.coords]})

# Limites des communes et arrondissements, découpées à l'emprise
adm = []
for k, n, r, c in osm:
    if k.startswith('adm'):
        ls = LineString(R4(c)).intersection(CLIP)
        for g in (ls.geoms if hasattr(ls, 'geoms') else [ls]):
            if g.geom_type == 'LineString' and not g.is_empty:
                adm.append([k, [[x, y] for x, y in g.coords]])

# Enregistre le fond de carte : plans d'eau, parcs, cours d'eau et limites
out = {'water': [rnd(g) for g in water], 'park': [rnd(g) for g in parks], 'rivers': rivers, 'adm': adm}
json.dump(out, open('data/build/basemap.json', 'w'), separators=(',', ':'))
print('plans d\'eau', len(water), '· parcs', len(parks), '· cours d\'eau', len(rivers), '· limites', len(adm))
