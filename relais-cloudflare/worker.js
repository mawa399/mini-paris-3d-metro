/**
 * Mini Paris 3D – relais temps réel PRIM (Cloudflare Worker)
 *
 * Joue en ligne le rôle de serveur.py : interroge l'API PRIM « estimated-timetable »
 * ligne par ligne et renvoie sur /api/temps-reel le même JSON simplifié
 * { updated, lines: { "1": [{id, dest, calls:[[gare, heure_ms], …]}], … }, errors }.
 *
 * La clé PRIM n'est jamais dans ce code : elle est stockée dans le secret PRIM_API_KEY
 * du Worker (Settings › Variables and Secrets).
 *
 * Pour ménager le quota PRIM et le temps de calcul du Worker, chaque ligne est gardée
 * en mémoire 60 s et au plus 4 lignes périmées sont rafraîchies par requête.
 *
 * La correspondance quais -> gares (realtime_refs.json) est lue une fois sur GitHub Pages.
 */
const REFS_URL = 'https://mawa399.github.io/mini-paris-3d-metro/data/build/realtime_refs.json';
let QUAYS = null;           // n° de quai IDFM -> nom de gare
let LINES = null;           // code ligne IDFM -> numéro de ligne
async function chargerRefs() {
  if (LINES) return;
  const r = await fetch(REFS_URL);
  if (!r.ok) throw new Error('realtime_refs.json : HTTP ' + r.status);
  const refs = await r.json();
  QUAYS = refs.quays; LINES = refs.lines;
}
const ORIGINES = ['https://mawa399.github.io', 'http://localhost:8765', 'http://127.0.0.1:8765'];
const PRIM = 'https://prim.iledefrance-mobilites.fr/marketplace/estimated-timetable?LineRef=STIF:Line::';
const TTL = 60_000;          // durée de vie d'une ligne en mémoire
const MAJ_PAR_REQUETE = 4;   // lignes rafraîchies au plus par requête

const memoire = new Map();   // code -> { t, journeys, error }

function quayName(ref) {
  const parts = (ref || '').replace(/^:+|:+$/g, '').split(':');
  return QUAYS[parts[parts.length - 1]] || null;
}

async function fetchLine(code, key) {
  const r = await fetch(PRIM + code + ':', { headers: { apikey: key, Accept: 'application/json' } });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const data = await r.json();
  const frames = data.Siri.ServiceDelivery.EstimatedTimetableDelivery[0].EstimatedJourneyVersionFrame || [];
  const now = Date.now();
  const journeys = [];
  for (const f of frames) {
    for (const vj of f.EstimatedVehicleJourney || []) {
      const calls = [];
      for (const c of (vj.EstimatedCalls && vj.EstimatedCalls.EstimatedCall) || []) {
        const name = quayName(c.StopPointRef && c.StopPointRef.value);
        const t = c.ExpectedArrivalTime || c.ExpectedDepartureTime;
        if (name && t) calls.push([name, Date.parse(t)]);
      }
      const futurs = calls.filter(c => c[1] > now - 60_000).sort((a, b) => a[1] - b[1]);
      if (!futurs.length) continue;
      let dest = quayName(vj.DestinationRef && vj.DestinationRef.value);
      if (!dest) dest = ((vj.DestinationName || [])[0] || {}).value || '';
      journeys.push({ id: (vj.DatedVehicleJourneyRef || {}).value || '', dest, calls: futurs });
    }
  }
  return journeys;
}

function entetes(req) {
  const o = req.headers.get('Origin') || '';
  return {
    'Access-Control-Allow-Origin': ORIGINES.includes(o) ? o : ORIGINES[0],
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Max-Age': '86400',
    'Vary': 'Origin',
  };
}

export default {
  async fetch(req, env) {
    const h = entetes(req);
    if (req.method === 'OPTIONS') return new Response(null, { headers: h });
    const url = new URL(req.url);
    if (!url.pathname.startsWith('/api/temps-reel')) {
      return new Response('Mini Paris 3D – relais temps réel PRIM. Données : /api/temps-reel', {
        headers: { ...h, 'Content-Type': 'text/plain; charset=utf-8' } });
    }
    const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
      status, headers: { ...h, 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
    if (!env.PRIM_API_KEY) return json({ error: 'Secret PRIM_API_KEY absent dans le Worker' }, 500);
    try { await chargerRefs(); } catch (e) { return json({ error: String(e.message || e) }, 502); }

    const now = Date.now();
    const perimees = Object.keys(LINES)
      .map(code => [code, (memoire.get(code) || { t: 0 }).t])
      .filter(([, t]) => now - t > TTL)
      .sort((a, b) => a[1] - b[1])
      .slice(0, MAJ_PAR_REQUETE)
      .map(([code]) => code);

    await Promise.all(perimees.map(async code => {
      const ancien = memoire.get(code);
      try {
        memoire.set(code, { t: now, journeys: await fetchLine(code, env.PRIM_API_KEY), error: null });
      } catch (e) {
        memoire.set(code, { t: now, journeys: ancien ? ancien.journeys : null, error: String(e.message || e).slice(0, 200) });
      }
    }));

    const etat = { updated: 0, lines: {}, errors: {} };
    for (const [code, ligne] of Object.entries(LINES)) {
      const m = memoire.get(code);
      if (!m) continue;
      if (m.journeys) { etat.lines[ligne] = m.journeys; if (!m.error) etat.updated = Math.max(etat.updated, m.t); }
      if (m.error) etat.errors[ligne] = m.error;
    }
    return json(etat);
  },
};
