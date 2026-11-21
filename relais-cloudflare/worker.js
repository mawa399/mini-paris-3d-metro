/**
 * Mini Paris 3D – relais temps réel PRIM (Cloudflare Worker)
 *
 * Joue en ligne le rôle de serveur.py : interroge l'API PRIM « estimated-timetable »
 * ligne par ligne et renvoie sur /api/temps-reel le même JSON simplifié
 * { updated, lines: { "1": [{id, dest, calls:[[gare, heure_ms], …]}], … }, errors, trafic }.
 *
 * « trafic » : les perturbations en cours sur les lignes de métro (API PRIM « disruptions_bulk »),
 * relues toutes les 2 minutes : { updated, lines: { "13": [{sev, cause, title, msg, stops, whole, end}] } }.
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
const TRAFIC_URL = 'https://prim.iledefrance-mobilites.fr/marketplace/disruptions_bulk/disruptions/v2';
const TRAFIC_TTL = 120_000;
let trafic = { t: 0, data: null, error: null };

// heure de Paris au format des périodes IDFM : 20261121T143000
function heureParis(ms) {
  const p = Object.fromEntries(new Intl.DateTimeFormat('fr-FR', { timeZone: 'Europe/Paris', hourCycle: 'h23',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
    .formatToParts(new Date(ms)).map(x => [x.type, x.value]));
  return `${p.year}${p.month}${p.day}T${p.hour}${p.minute}${p.second}`;
}
const ENTITES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };
function texte(html) {
  return (html || '')
    .replace(/<br\s*\/?>/gi, '\n').replace(/<\/p>/gi, '\n').replace(/<[^>]+>/g, '')
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d))
    .replace(/&([a-z]+);/gi, (m, n) => ENTITES[n.toLowerCase()] ?? m)
    .replace(/\n{3,}/g, '\n\n').trim();
}
// ne garde que les perturbations du métro actives maintenant
function simplifierTrafic(bulk, now) {
  const maintenant = heureParis(now);
  const parId = new Map(bulk.disruptions.map(d => [d.id, d]));
  const lines = {};
  for (const l of bulk.lines || []) {
    if (l.mode !== 'Metro') continue;
    const code = (l.id || '').split(':').pop();
    const ligne = LINES[code];
    if (!ligne) continue;
    const parDisruption = new Map();
    for (const o of l.impactedObjects || []) {
      for (const id of o.disruptionIds || []) {
        if (!parDisruption.has(id)) parDisruption.set(id, { stops: new Set(), whole: false });
        const x = parDisruption.get(id);
        if (o.type === 'line') x.whole = true; else if (o.name) x.stops.add(o.name);
      }
    }
    const out = [];
    for (const [id, x] of parDisruption) {
      const d = parId.get(id);
      if (!d) continue;
      const periode = (d.applicationPeriods || []).find(p => p.begin <= maintenant && maintenant <= p.end);
      if (!periode) continue;
      out.push({ sev: d.severity, cause: d.cause, title: d.title, msg: texte(d.message), short: d.shortMessage || '',
        stops: [...x.stops], whole: x.whole, end: periode.end });
    }
    const rang = { BLOQUANTE: 0, PERTURBEE: 1, INFORMATION: 2 };
    if (out.length) lines[ligne] = out.sort((a, b) => (rang[a.sev] ?? 3) - (rang[b.sev] ?? 3));
  }
  return { updated: now, lines };
}
async function rafraichirTrafic(key, now) {
  if (now - trafic.t < TRAFIC_TTL) return;
  trafic.t = now;
  try {
    const r = await fetch(TRAFIC_URL, { headers: { apikey: key, Accept: 'application/json' } });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    trafic = { t: now, data: simplifierTrafic(await r.json(), now), error: null };
  } catch (e) {
    trafic = { t: now, data: trafic.data, error: String(e.message || e).slice(0, 200) };
  }
}

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

    const majTrafic = rafraichirTrafic(env.PRIM_API_KEY, now);
    await Promise.all(perimees.map(async code => {
      const ancien = memoire.get(code);
      try {
        memoire.set(code, { t: now, journeys: await fetchLine(code, env.PRIM_API_KEY), error: null });
      } catch (e) {
        memoire.set(code, { t: now, journeys: ancien ? ancien.journeys : null, error: String(e.message || e).slice(0, 200) });
      }
    }));

    await majTrafic;
    const etat = { updated: 0, lines: {}, errors: {}, trafic: trafic.data };
    if (trafic.error) etat.errors.trafic = trafic.error;
    for (const [code, ligne] of Object.entries(LINES)) {
      const m = memoire.get(code);
      if (!m) continue;
      if (m.journeys) { etat.lines[ligne] = m.journeys; if (!m.error) etat.updated = Math.max(etat.updated, m.t); }
      if (m.error) etat.errors[ligne] = m.error;
    }
    return json(etat);
  },
};
