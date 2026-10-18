"""Assemble les tronçons IDFM en itinéraires terminus -> terminus pour chaque ligne de métro.

Entrées : data/sources/segs.json   [[indice, couleur, [[ [lon,lat], ... ]]], ...]
          data/sources/stations.json [[nom, indice, lon, lat, id_zdc], ...]
Sortie  : data/build/network.json
"""
import json, math
import networkx as nx

segs = json.load(open('data/sources/segs.json'))
stations = json.load(open('data/sources/stations.json'))

norm = lambda s: {'7bis': '7b', '3bis': '3b', '7b': '7b', '3b': '3b'}.get(s, s)

TERMINI = {
    '1': [('La Défense', 'Château de Vincennes')],
    '2': [('Porte Dauphine', 'Nation')],
    '3': [('Pont de Levallois', 'Gallieni')],
    '3b': [('Gambetta', 'Porte des Lilas')],
    '4': [('Porte de Clignancourt', 'Bagneux - Lucie Aubrac')],
    '5': [('Bobigny Pablo Picasso', "Place d'Italie")],
    '6': [('Charles De Gaulle-Étoile', 'Nation')],
    '7': [('La Courneuve 8 Mai 1945', 'Villejuif - Louis Aragon'),
          ('La Courneuve 8 Mai 1945', "Mairie d'Ivry")],
    '7b': [('Louis Blanc', 'Pré Saint-Gervais')],
    '8': [('Balard', 'Créteil - Pointe du Lac')],
    '9': [('Pont de Sèvres', 'Mairie de Montreuil')],
    '10': [('Boulogne - Pont de Saint-Cloud', "Gare d'Austerlitz")],
    '11': [('Châtelet', 'Rosny-Bois-Perrier')],
    '12': [("Mairie d'Aubervilliers", "Mairie d'Issy")],
    '13': [('Saint-Denis - Université', 'Châtillon-Montrouge'),
           ('Les Courtilles', 'Châtillon-Montrouge')],
    '14': [('Saint-Denis – Pleyel', "Aéroport d'Orly")],
}

R = 6371008.8
def dist(a, b):
    la = math.radians((a[1] + b[1]) / 2)
    dx = math.radians(b[0] - a[0]) * math.cos(la) * R
    dy = math.radians(b[1] - a[1]) * R
    return math.hypot(dx, dy)

def plen(p):
    return sum(dist(p[i], p[i + 1]) for i in range(len(p) - 1))

network = []
for line, routes in TERMINI.items():
    lsegs = [s for s in segs if norm(s[0]) == line]
    color = '#' + lsegs[0][1]
    # --- graphe : nœuds = extrémités regroupées à moins de 30 m
    nodes = []
    def node_of(pt):
        for i, n in enumerate(nodes):
            if dist(n, pt) < 30:
                return i
        nodes.append(pt)
        return len(nodes) - 1
    G = nx.Graph()
    for s in lsegs:
        for part in s[2]:
            if len(part) < 2:
                continue
            a, b = node_of(part[0]), node_of(part[-1])
            if a == b:
                continue
            w = plen(part)
            # garde le tronçon le plus court s'il y a doublon
            if G.has_edge(a, b) and G[a][b]['w'] <= w:
                continue
            G.add_edge(a, b, w=w, geom=part, ends=(a, b))
    # relie les composantes séparées par de petits trous dans les tracés
    bridged = 0
    while nx.number_connected_components(G) > 1:
        comps = sorted(nx.connected_components(G), key=len, reverse=True)
        main = comps[0]
        best = None
        for c in comps[1:]:
            for u in c:
                for v in main:
                    d = dist(nodes[u], nodes[v])
                    if best is None or d < best[0]:
                        best = (d, u, v)
        if best[0] > 600:
            print(f'  ligne {line}: trou de {best[0]:.0f} m non comblé')
            break
        d, u, v = best
        G.add_edge(u, v, w=d, geom=[nodes[u], nodes[v]], ends=(u, v))
        bridged += 1
    if bridged:
        print(f'  ligne {line}: {bridged} raccord(s)')
    lst = [s for s in stations if norm(s[1]) == line]

    def nearest_node(name):
        cand = [s for s in lst if s[0] == name]
        if not cand:
            raise SystemExit(f'Gare introuvable ligne {line}: {name}')
        p = (cand[0][2], cand[0][3])
        return min(G.nodes, key=lambda n: dist(nodes[n], p))

    for (a_name, b_name) in routes:
        a, b = nearest_node(a_name), nearest_node(b_name)
        path_nodes = nx.shortest_path(G, a, b, weight='w')
        coords = []
        for u, v in zip(path_nodes, path_nodes[1:]):
            e = G[u][v]
            g = list(e['geom'])
            if e['ends'][0] != u:
                g = g[::-1]
            coords.extend(g if not coords else g[1:])
        # distances cumulées
        cum = [0.0]
        for i in range(1, len(coords)):
            cum.append(cum[-1] + dist(coords[i - 1], coords[i]))

        # projette chaque gare de la ligne sur l'itinéraire
        def project(p):
            best = (1e18, 0.0)
            for i in range(len(coords) - 1):
                ax, ay = coords[i]; bx, by = coords[i + 1]
                la = math.cos(math.radians(ay))
                vx, vy = (bx - ax) * la, by - ay
                wx, wy = (p[0] - ax) * la, p[1] - ay
                L2 = vx * vx + vy * vy
                t = 0 if L2 == 0 else max(0, min(1, (wx * vx + wy * vy) / L2))
                q = (ax + (bx - ax) * t, ay + (by - ay) * t)
                d = dist(q, p)
                if d < best[0]:
                    best = (d, cum[i] + t * (cum[i + 1] - cum[i]))
            return best
        stops = {}
        for s in lst:
            d, along = project((s[2], s[3]))
            if d < 120:
                # une gare par nom (les quais d'une même gare sont fusionnés)
                if s[0] not in stops or d < stops[s[0]][1]:
                    stops[s[0]] = (along, d)
        stop_list = sorted(((v[0], k) for k, v in stops.items()))
        # dédoublonne les gares trop proches (< 80 m le long de la voie)
        clean = []
        for along, name in stop_list:
            if clean and along - clean[-1][0] < 80:
                continue
            clean.append((along, name))
        network.append({
            'line': line,
            'color': color,
            'from': a_name, 'to': b_name,
            'path': [[round(x, 5), round(y, 5)] for x, y in coords],
            'cum': [round(c, 1) for c in cum],
            'stops': [[name, round(al, 1)] for al, name in clean],
            'length': round(cum[-1]),
        })
        print(f"{line:>3} {a_name} → {b_name}: {cum[-1]/1000:.1f} km, {len(clean)} gares")

json.dump(network, open('data/build/network.json', 'w'), ensure_ascii=False, separators=(',', ':'))
