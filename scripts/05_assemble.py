"""Assemble la page autonome index.html à partir du gabarit et des données construites."""
import json

ORDER = ['1', '2', '3', '3b', '4', '5', '6', '7', '7b', '8', '9', '10', '11', '12', '13', '14']
norm = lambda s: {'7bis': '7b', '3bis': '3b'}.get(s, s)

net = json.load(open('data/build/network.json'))
base = json.load(open('data/build/basemap.json'))
sched = open('data/build/schedule.json').read()
st = json.load(open('data/sources/stations.json'))

groups = {}
for name, line, lon, lat, zdc in st:
    g = groups.setdefault(zdc or name, {'n': name, 'pts': [], 'l': set()})
    g['pts'].append((lon, lat)); g['l'].add(norm(line))
stations = [{'n': g['n'],
             'p': [round(sum(p[0] for p in g['pts']) / len(g['pts']), 5), round(sum(p[1] for p in g['pts']) / len(g['pts']), 5)],
             'l': sorted(g['l'], key=ORDER.index)} for g in groups.values()]

html = open('scripts/template.html').read()
html = (html.replace('__NETWORK__', json.dumps(net, ensure_ascii=False, separators=(',', ':')))
            .replace('__BASEMAP__', json.dumps(base, separators=(',', ':')))
            .replace('__STATIONS__', json.dumps(stations, ensure_ascii=False, separators=(',', ':')))
            .replace('__SCHEDULE__', sched))
# document complet pour l'ouvrir hors de claude.ai (GitHub Pages, serveur local)
doc = ('<!doctype html>\n<html lang="fr">\n<head>\n<meta charset="utf-8">\n'
       '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n</head>\n<body>\n'
       + html + '\n</body>\n</html>\n')
open('index.html', 'w').write(doc)
print(len(stations), 'gares ·', len(doc) // 1024, 'Ko → index.html')
