"""Assemble la page autonome index.html à partir du gabarit et des données construites."""
import json

# Ordre d'affichage des lignes
ORDER = ['1', '2', '3', '3b', '4', '5', '6', '7', '7b', '8', '9', '10', '11', '12', '13', '14']
# Uniformise les noms des lignes bis
norm = lambda s: {'7bis': '7b', '3bis': '3b'}.get(s, s)

# Données produites par les scripts précédents
net = json.load(open('data/build/network.json'))       # itinéraires des lignes (script 02)
base = json.load(open('data/build/basemap.json'))      # fond de carte (script 03)
sched = open('data/build/schedule.json').read()        # horaires (script 04), insérés tels quels
st = json.load(open('data/sources/stations.json'))     # gares (script 01)

# Regroupe les gares par zone de correspondance : une gare desservie par plusieurs lignes
# (ex. Châtelet) devient un seul point, placé au centre de ses quais
groups = {}
for name, line, lon, lat, zdc in st:
    g = groups.setdefault(zdc or name, {'n': name, 'pts': [], 'l': set()})
    g['pts'].append((lon, lat)); g['l'].add(norm(line))
stations = [{'n': g['n'], 'z': str(z),
             'p': [round(sum(p[0] for p in g['pts']) / len(g['pts']), 5), round(sum(p[1] for p in g['pts']) / len(g['pts']), 5)],
             'l': sorted(g['l'], key=ORDER.index)} for z, g in groups.items()]
# Analyses (script 06) : fréquentation et accessibilité, sans les gares vides
enrich = json.load(open('data/build/enrich.json', encoding='utf-8'))
enrich = {k: v for k, v in enrich.items() if v}

# Remplace les marqueurs du gabarit par les données, pour obtenir une seule page autonome
html = open('scripts/gabarit_carte.html').read()
html = (html.replace('__NETWORK__', json.dumps(net, ensure_ascii=False, separators=(',', ':')))
            .replace('__BASEMAP__', json.dumps(base, separators=(',', ':')))
            .replace('__STATIONS__', json.dumps(stations, ensure_ascii=False, separators=(',', ':')))
            .replace('__ENRICH__', json.dumps(enrich, separators=(',', ':')))
            .replace('__SCHEDULE__', sched))
# document complet pour l'ouvrir hors de claude.ai (GitHub Pages, serveur local)
doc = ('<!doctype html>\n<html lang="fr">\n<head>\n<meta charset="utf-8">\n'
       '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n</head>\n<body>\n'
       + html + '\n</body>\n</html>\n')
open('index.html', 'w').write(doc)
print(len(stations), 'gares ·', len(doc) // 1024, 'Ko → index.html')
