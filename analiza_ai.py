"""Stratul opțional de interpretare AI — narativ, nu verdict.

Sinteza din capul paginii rămâne deterministă. Acest modul poate produce, numai
la cerere explicită, o analiză LLM auditabilă cu promptul, digestul exact și
modelul folosit. Configurarea cheii nu pornește singură niciun apel AI.

Activare de bază (orice API OpenAI-compatibil):
    AI_API_KEY=... [AI_MODEL=...] [AI_BASE_URL=...] python3 analiza_ai.py

Comparația cu surse web este deliberat opt-in și disponibilă numai prin API-ul
OpenAI oficial, deoarece are cost/latency suplimentare și trebuie să întoarcă
citări verificabile. Și în acest mod analiza rulează numai la cerere:
    AI_WEB_SEARCH=1 [AI_WEB_MODEL=gpt-5.6-terra] python3 analiza_ai.py
"""

import hashlib
import json
import os
import re
import threading
import urllib.parse
import urllib.request
from datetime import date

import anomalii
import connectors as C

PROMPT_VERSION = 14

_lock_ai = threading.Lock()

AI_BASE = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")
AI_WEB_MODEL = os.environ.get("AI_WEB_MODEL", "gpt-5.6-terra")
AI_WEB_SEARCH = os.environ.get("AI_WEB_SEARCH", "").strip().lower() in {
    "1", "true", "yes", "on",
}

PROMPT_SISTEM = """Ești un analist hidrologic riguros și sobru. Primești un JSON auditabil cu starea Dunării. Scopul tău este să descrii situația, să cauți contradicții și anomalii de date și să delimitezi ce este susținut de probe de ceea ce rămâne necunoscut.

Dicționarul câmpurilor:
- azi.value, azi_m3s, debit, masurat_m3s și model_m3s sunt DEBITE în m³/s, nu niveluri;
- pct / percentila debitului este rangul față de istoricul modelului din fereastra calendaristică ±7 zile în jurul datei: P0 minim, P50 mediană, P100 maxim; nu este procent din debit; ani_mai_mici folosește separat aceeași dată exactă;
- streak_sub_p10 = zile consecutive sub P10;
- abatere_pct = abaterea procentuală față de mediana istorică a zilei;
- lipsa_km3 = volum cumulat lipsă față de mediană;
- anomalie_km3 la GRACE = abaterea apei totale față de referință, nu debit;
- z este scor standardizat; |z| ≤ 1,5 este în variabilitatea istorică a testului.
- prognoza_afluenti_inhga conține benzi de prognoză, nu valori măsurate;
- statistici_afluenti_masurati conține observații instantanee brute în secțiuni parțiale; acestea nu sunt medii zilnice și nici aporturi totale în Dunăre.
- climatologie_modelata_afluenti compară valoarea GloFAS exclusiv cu istoricul aceleiași celule și aceluiași model; percentila ei nu este percentila măsurătorii DanubeHIS.
- context_resurse_apa_anar este un comunicat oficial structurat, nu o serie zilnică: folosește-l drept curent numai dacă current=true; un coeficient sau volum null rămâne nepublicat.
- stare_sen este o fotografie instantanee Transelectrica; istoric_sen_local conține câte o fotografie locală pe zi, nu medii zilnice și nu o climatologie.
- context_piata_energie separă patru produse oficiale: consumption este consum brut realizat/prognozat; reserve_procurement este capacitate de echilibrare contractată, nu rezervă disponibilă în timp real; balancing conține dezechilibru/prețuri estimate și rezervă activată, nu marja rămasă; day_ahead este preț PZU pentru ziua următoare, nu cauza unei variații și nu preț final la consumator.
- stare_cne_snn poate confirma starea și cauza declarată de operator; nu completează nivelul bazinului de aspirație sau pragurile operaționale dacă acestea lipsesc.

Reguli stricte:
1. Pentru starea monitorului și toate valorile numerice hidrologice folosește exclusiv JSON-ul. Nu inventa și nu completa din memorie valori, stații, date, ani sau procente. Spune explicit când lipsesc.
2. Păstrează tipul fiecărei probe: măsurătoare in-situ, model, reanaliză, măsurătoare orbitală, clasificare satelitară ori catalog_only. O granula prezentă în catalog nu este o valoare observată sau ingerată.
3. Consultă registru_provenienta. Sursele din aceeași familie/dependență se numără o singură dată; două căi de livrare ale aceleiași măsurători nu sunt confirmări independente.
4. Verifică erori_calcul și metadate_actualizare înaintea concluziilor. O sursă absentă, stale sau cu eroare reduce acoperirea; nu este o anomalie a fluviului.
5. Încredere mare este permisă numai când există cel puțin două familii de probe independente, direct relevante, fără contradicție materială. Pentru fiecare cauză arată proba, contra-proba, limita și nivelul de încredere.
6. Nu transforma o corelație, un reziduu sau o nepotrivire măsurat/model în cauzalitate. O contradicție indică mai întâi probleme posibile de timp, poziție, unitate, parsare, bias ori prospețime.
7. Dacă testele sunt în limite, spune doar că nu apare o incompatibilitate în testele disponibile; nu susține că un fenomen nemăsurat a fost exclus.
8. Bilanțul P−Q folosește șase puncte-proxy și debit modelat, nu este închidere hidrologică de bazin. Nu poate izola stocarea, evapotranspirația, captările, transferurile sau eroarea de model.
9. Dacă mod_verificare_externa.activ este true, folosește căutarea web numai pentru context oficial/instituțional actual și contradicții. Încearcă să consulți cel puțin două familii instituționale primare independente, când există. Etichetează separat „validarea sursei deja ingerate” și „contraproba externă independentă”. Redeschiderea pe web a aceleiași pagini sau a aceleiași măsurători din JSON validează livrarea, dar nu adaugă independență; dacă nu găsești o a doua familie, spune explicit asta. Indică data, păstrează citările instrumentului și nu înlocui cifrele monitorului cu valori web. Dacă modul este false sau instrumentul nu a rulat, scrie exact că verificarea externă nu a fost efectuată; nu prezenta memoria modelului ca verificare externă.
10. Nu numi toate datele „de azi” dacă observațiile au date diferite. În SITUAȚIA precizează intervalul datelor disponibile și atașează data fiecărei comparații materiale, mai ales model versus măsurătoare.
11. La Baziaș, reconciliere_bazias.valoare_oficiala_curenta este cifra canonică a monitorului pentru starea curentă. reper_modelat_climatologic este debitul simulat într-o celulă GloFAS de aproximativ 5 km, nu o a doua măsurătoare și nu trebuie să egaleze valoarea INHGA. Folosește GloFAS față de propria climatologie. O diferență absolută compatibilă cu biasul istoric nu este „contradicție”; numește ruptură numai schimbarea relației pe perechi cu aceeași dată, susținută de test.
12. Pentru afluenții românești, separă prognoza_afluenti_inhga, statistici_afluenti_masurati și climatologie_modelata_afluenti. Cele cinci secțiuni măsurate au acoperire parțială în bazin și valori instantanee brute: nu se însumează, nu se integrează în km³ și nu estimează aportul total al României în Dunăre. Comparația măsurată cu aceeași fereastră din anul anterior descrie un singur an, nu climatologia. GloFAS poate clasifica raritatea numai în propriul model; diferența absolută măsurat/model poate fi bias local și nu transferă percentila modelului asupra măsurătorii.
13. Fără speculații politice sau acuzații la adresa țărilor, instituțiilor ori operatorilor.
14. Pentru concluzii despre România și criticitatea energetică, confruntă context_resurse_apa_anar, stare_cne_snn, stare_sen, istoric_sen_local și context_piata_energie. Lipsa restricțiilor pentru apa populației nu anulează restricțiile sectoriale; o oprire CNE nu dovedește singură o criză SEN; un istoric local sub minimum_days sau cu enough_for_comparison=false nu poate susține comparații. Nu numi rezervele contractate „disponibile”, nu scădea rezerva activată pentru a inventa o marjă rămasă și nu atribui un preț PZU ori de dezechilibru Cernavodă fără o probă cauzală separată.

Răspunsul trebuie să aibă exact aceste șase titluri, în română. Țintește 500–550 de cuvinte și nu depăși 600; rezervă spațiu pentru toate secțiunile înainte de a detalia:
SITUAȚIA
CAUZE PROBABILE
ANOMALII DE DATE ȘI CONTRADICȚII
VERIFICARE EXTERNĂ
CE NU SE POATE CONCLUZIONA DIN ACESTE DATE
CE AR SCHIMBA CONCLUZIA"""

REQUIRED_HEADINGS = (
    "SITUAȚIA",
    "CAUZE PROBABILE",
    "ANOMALII DE DATE ȘI CONTRADICȚII",
    "VERIFICARE EXTERNĂ",
    "CE NU SE POATE CONCLUZIONA DIN ACESTE DATE",
    "CE AR SCHIMBA CONCLUZIA",
)


def _delivery_meta(result):
    """Păstrează proveniența temporală a wrapperelor de cache."""
    if not isinstance(result, dict):
        return {}
    return {k: result[k] for k in ("stale", "cache_age_s", "error") if k in result}


def _safe_context(fetch_fn):
    """O sursă de context căzută nu trebuie să anuleze întregul digest."""
    try:
        result = fetch_fn()
        if isinstance(result, dict) and "data" in result and (
                "stale" in result or "cache_age_s" in result):
            return {"date": result["data"], "livrare": _delivery_meta(result)}
        return {"date": result, "livrare": {}}
    except Exception as exc:
        return {"date": None, "livrare": {"eroare": str(exc)[:240]}}


def _inhga():
    try:
        return C.inhga_bulletin()["data"]
    except Exception:
        return {}


def _digest():
    """Datele necesare analizei, fără seriile lungi sau imaginile binare."""
    raport = C.cached(anomalii.REPORT_CACHE_KEY, 6 * 3600, anomalii.report)
    stats = C.cached(anomalii.STATS_CACHE_KEY, 6 * 3600, anomalii.full_stats)
    budget = C.cached(anomalii.BUDGET_CACHE_KEY, 6 * 3600, anomalii.water_budget)
    r, st, bi = raport["data"], stats["data"], budget["data"]
    inhga = _inhga()

    def fara(value, *keys):
        return {k: v for k, v in (value or {}).items() if k not in keys}

    bazias_model = next((c for c in r.get("climatologie", [])
                         if c.get("id") == "bazias"), {})
    relatie = r.get("masurat_vs_model") or {}
    reconciliere_bazias = {
        "valoare_oficiala_curenta": {
            "tip_proba": "valoare_oficiala_in_situ",
            "sursa": "INHGA, buletinul zilnic",
            "data": inhga.get("data_buletin"),
            "debit_m3s": inhga.get("debit_bazias_m3s"),
            "rol": "cifra canonică a monitorului pentru starea curentă la Baziaș",
        },
        "reper_modelat_climatologic": {
            "tip_proba": "model_hidrologic",
            "sursa": bazias_model.get("sursa", "GloFAS v4 via Open-Meteo Flood API"),
            "data": (bazias_model.get("azi") or {}).get("date"),
            "debit_m3s": (bazias_model.get("azi") or {}).get("value"),
            "rezolutie_spatiala_aprox_km": bazias_model.get(
                "rezolutie_spatiala_aprox_km", 5),
            "celula_model": bazias_model.get("celula_model"),
            "rol": "percentile și comparații numai față de climatologia aceluiași model",
        },
        "date_curente_aliniate": (
            bool(inhga.get("data_buletin"))
            and inhga.get("data_buletin") == (bazias_model.get("azi") or {}).get("date")
        ),
        "ultima_pereche_aceeasi_data": relatie.get("ultima_pereche_aceeasi_data"),
        "test_relatie_masurat_model": fara(relatie, "serie"),
        "regula": ("INHGA descrie starea curentă; GloFAS este reper modelat de grilă. "
                   "Biasul stabil nu este contradicție sau anomalie a fluviului."),
    }

    return {
        "data": date.today().isoformat(),
        "mod_verificare_externa": {
            "activ": AI_WEB_SEARCH,
            "regula": "web doar pentru context oficial actual; cifrele monitorului rămân din JSON",
        },
        "metadate_actualizare": {
            "raport_anomalii": _delivery_meta(raport),
            "statistici": _delivery_meta(stats),
            "bilant_apa": _delivery_meta(budget),
        },
        "erori_calcul": r.get("erori", {}),
        "climatologie_sectiuni": [fara(c, "recent") for c in r.get("climatologie", [])],
        "statistici_debit_sectiuni": st.get("debit"),
        "metode_statistici": st.get("metoda"),
        "bilant_portile_de_fier": r.get("bilant"),
        "inhga_vs_model": fara(r.get("masurat_vs_model"), "serie"),
        "reconciliere_bazias": reconciliere_bazias,
        "mire_incrucisate": r.get("mire_crosscheck"),
        "precipitatii_vs_debit": r.get("precipitatii"),
        "satelit_altimetrie": r.get("satelit"),
        "germania_masurat_vs_model": r.get("germania"),
        "ungaria_masurat_vs_model": r.get("ungaria"),
        "serbia_masurat_vs_model": r.get("serbia"),
        "austria_test_retentie": fara(r.get("austria"), "statii"),
        "bilant_apa_bazin_superior": bi,
        "statistici_precipitatii_zone": st.get("precipitatii"),
        "buletin_inhga": fara(inhga, "text_oficial"),
        "prognoza_afluenti_inhga": _safe_context(C.inhga_danube_tributaries),
        "statistici_afluenti_masurati": _safe_context(C.danubehis_romanian_tributaries),
        "climatologie_modelata_afluenti": _safe_context(
            C.glofas_romanian_tributary_climatology),
        "context_resurse_apa_anar": _safe_context(C.anar_water_resources),
        "stare_cne_snn": _safe_context(C.snn_cernavoda_status),
        "stare_sen": _safe_context(C.sen_live),
        "istoric_sen_local": _safe_context(C.sen_history_context),
        "context_piata_energie": _safe_context(C.sen_market_context),
        "registru_provenienta": C.evidence_source_registry(),
        "context_seceta_copernicus_edo": _safe_context(C.edo_status),
        "context_suprafata_apa_opera": _safe_context(C.opera_surface_status),
        "context_zapada_sol_copernicus_land": _safe_context(C.copernicus_land_context),
        "catalog_misiuni_satelitare_nasa": _safe_context(C.earthdata_satellite_catalog),
        "context_istoric_masurat_grdc": _safe_context(anomalii.grdc_context),
    }


def _amprenta_stare():
    """Rezumat categorial păstrat în rezultat pentru comparații între rulări."""
    r = C.cached(anomalii.REPORT_CACHE_KEY, 6 * 3600, anomalii.report)["data"]
    bi = C.cached(anomalii.BUDGET_CACHE_KEY, 6 * 3600, anomalii.water_budget)["data"]

    def z_ok(value, prag=1.5):
        return None if not value or value.get("insuficient") else abs(value.get("z", 0)) <= prag

    inhga = _inhga()
    anar_context = _safe_context(C.anar_water_resources).get("date") or {}
    snn_context = _safe_context(C.snn_cernavoda_status).get("date") or {}
    sen_history = C.sen_history_context()
    market_context = C.sen_market_context()
    debit, medie = inhga.get("debit_bazias_m3s"), inhga.get("media_multianuala_m3s")
    normal = (bi.get("bazias") or {}).get("normal_km3")
    lipsa = (bi.get("bazias") or {}).get("lipsa_km3")
    parti = {
        "versiune_metoda": PROMPT_VERSION,
        "configuratie_ai": {
            "mod": "web_cu_citari" if AI_WEB_SEARCH else "doar_json",
            "model": AI_WEB_MODEL if AI_WEB_SEARCH else AI_MODEL,
        },
        "severitati": [c.get("severitate") for c in r.get("climatologie", [])],
        "erori": sorted((r.get("erori") or {}).keys()),
        "verificari": {
            "bilant_pf": z_ok(r.get("bilant")),
            "inhga_model": z_ok(r.get("masurat_vs_model")),
            "mire": (r.get("mire_crosscheck") or {}).get("mediana_abatere_cm", 99) <= 10,
            "satelit": (r.get("satelit") or {}).get("mediana_pct", 99) <= 15,
            "germania": (r.get("germania") or {}).get("coerent"),
            "ungaria": (r.get("ungaria") or {}).get("coerent"),
            "serbia": (r.get("serbia") or {}).get("coerent"),
            "austria": not (r.get("austria") or {}).get("suspiciune_retentie", False),
        },
        "bilant_gaura_pct5": round(100 * lipsa / normal / 5) if normal and lipsa is not None else None,
        "grace_luna": (bi.get("grace") or {}).get("luna"),
        "inhga_pct10": round(10 * debit / medie) if debit and medie else None,
        "anar": {
            "published": anar_context.get("published"),
            "current": anar_context.get("current"),
            "fill_pct": (anar_context.get("reservoirs") or {}).get("fill_pct"),
            "drinking_restrictions": (anar_context.get("restrictions") or {}).get("drinking_water"),
        },
        "cernavoda_snn": {
            "date": snn_context.get("date"), "u1": snn_context.get("u1"),
            "u2": snn_context.get("u2"), "water_related": snn_context.get("water_related"),
            "needs_review": snn_context.get("needs_review"),
        },
        "sen_history_ready": bool(sen_history.get("enough_for_comparison")),
        "piata_energie": {
            "components": market_context.get("available_components"),
            "consumption_date": (market_context.get("consumption") or {}).get("delivery_date"),
            "reserve_date": (market_context.get("reserve_procurement") or {}).get("delivery_date"),
            "reserve_min_pct5": (round((market_context.get("reserve_procurement") or {})
                                       .get("minimum_satisfaction_pct", 0) / 5)
                                  if (market_context.get("reserve_procurement") or {})
                                  .get("minimum_satisfaction_pct") is not None else None),
            "balancing_interval": ((market_context.get("balancing") or {})
                                   .get("latest_interval") or {}).get("to"),
            "pzu_delivery": (market_context.get("day_ahead") or {}).get("delivery_date"),
            "pzu_base_100": (round((market_context.get("day_ahead") or {})
                                   .get("base_lei_mwh", 0) / 100)
                             if (market_context.get("day_ahead") or {})
                             .get("base_lei_mwh") is not None else None),
        },
    }
    fp = hashlib.sha256(json.dumps(parti, sort_keys=True).encode()).hexdigest()[:16]
    return parti, fp


def _ai_key():
    key = os.environ.get("AI_API_KEY", "").strip()
    if key:
        return key
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "keys", "openai.key")
    if os.path.isfile(path):
        with open(path) as handle:
            return handle.read().strip()
    return ""


def _official_openai_base():
    parsed = urllib.parse.urlparse(AI_BASE)
    return parsed.scheme == "https" and parsed.hostname == "api.openai.com"


def _request_spec(date_intrare):
    """Construiește cererea fără a o trimite; util și pentru audit/teste."""
    user_text = "Datele monitorului (cu date proprii fiecărei probe):\n" + json.dumps(
        date_intrare, ensure_ascii=False)
    if AI_WEB_SEARCH:
        return {
            "mode": "web_cu_citari",
            "model": AI_WEB_MODEL,
            "url": f"{AI_BASE}/responses",
            "body": {
                "model": AI_WEB_MODEL,
                "instructions": PROMPT_SISTEM,
                "input": user_text,
                "text": {"verbosity": "low"},
                "tools": [{"type": "web_search", "search_context_size": "medium"}],
                "tool_choice": "required",
                "max_output_tokens": 3000,
            },
        }
    return {
        "mode": "doar_json",
        "model": AI_MODEL,
        "url": f"{AI_BASE}/chat/completions",
        "body": {
            "model": AI_MODEL,
            "temperature": 0.2,
            "max_tokens": 1200,
            "messages": [
                {"role": "system", "content": PROMPT_SISTEM},
                {"role": "user", "content": user_text},
            ],
        },
    }


def _parse_responses_output(out):
    """Extrage textul, căutările și citările URL din Responses API."""
    text_parts, raw_citations, queries = [], [], []
    for item in out.get("output") or []:
        if item.get("type") == "web_search_call":
            action = item.get("action") or {}
            candidates = action.get("queries") or [action.get("query")]
            queries.extend(q for q in candidates if isinstance(q, str) and q)
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") != "output_text":
                continue
            part = content.get("text") or ""
            offset = sum(len(p) for p in text_parts)
            text_parts.append(part)
            for ann in content.get("annotations") or []:
                if ann.get("type") != "url_citation":
                    continue
                url = ann.get("url") or ""
                if not url.startswith("https://"):
                    continue
                raw_citations.append({
                    "url": url,
                    "title": ann.get("title") or url,
                    "start": offset + min(max(int(ann.get("start_index", 0)), 0), len(part)),
                    "end": offset + min(max(int(ann.get("end_index", len(part))), 0), len(part)),
                })

    text = "".join(text_parts) or (out.get("output_text") or "")
    citations, ids_by_url, replacements = [], {}, {}
    for citation in raw_citations:
        if citation["url"] not in ids_by_url:
            cid = len(citations) + 1
            ids_by_url[citation["url"]] = cid
            citations.append({"id": cid, "url": citation["url"],
                              "title": citation["title"]})
        cid = ids_by_url[citation["url"]]
        start, end = sorted((citation["start"], citation["end"]))
        replacements.setdefault((start, end), set()).add(cid)
    # Anotația acoperă citarea deja produsă de API (de regulă un link
    # Markdown). O înlocuim cu markerul nostru, nu o dublăm după același text.
    for (start, end), ids in sorted(replacements.items(), reverse=True):
        markers = "".join(f"⟦WEB:{cid}⟧" for cid in sorted(ids))
        text = text[:start] + markers + text[end:]
    return text.strip(), citations, list(dict.fromkeys(queries))


def _normalize_response(text):
    """Normalizează numai titlurile, fără a rescrie analiza modelului."""
    patterns = {
        "SITUAȚIA": r"SITUAȚIA",
        "CAUZE PROBABILE": r"CAUZE PROBABILE",
        "ANOMALII DE DATE ȘI CONTRADICȚII": r"ANOMALII DE DATE ȘI CONTRADICȚII",
        "VERIFICARE EXTERNĂ": r"VERIFICARE EXTERNĂ",
        "CE NU SE POATE CONCLUZIONA DIN ACESTE DATE":
            r"CE NU SE POATE CONCLUZIONA DIN ACESTE DATE",
        "CE AR SCHIMBA CONCLUZIA": r"CE AR SCHIMB[ĂA] CONCLUZIA",
    }
    normalized = text
    for heading, pattern in patterns.items():
        normalized = re.sub(
            rf"(?mi)^\s*(?:#{{1,6}}\s*)?(?:\*\*)?{pattern}\s*:?\s*(?:\*\*)?\s*$",
            heading, normalized,
        )
    missing = [heading for heading in REQUIRED_HEADINGS if heading not in normalized]
    return normalized.strip(), missing


def analiza(run=False):
    """Rulează analiza numai când apelantul cere explicit ``run=True``.

    Răspunsul implicit este intenționat doar un status: nu citește analiza
    precedentă și, mai important, nu poate declanșa accidental un apel plătit.
    """
    if not run:
        return {
            "activ": False,
            "manual_only": True,
            "motiv": "Analiza AI este ascunsă din interfață și rulează numai la "
                     "cerere explicită (python3 analiza_ai.py). Sinteza vizibilă "
                     "rămâne deterministă.",
        }

    key = _ai_key()
    if not key:
        return {
            "activ": False,
            "motiv": "Lipsește AI_API_KEY. Sinteza deterministă funcționează fără AI; "
                     "pentru analiza narativă setați AI_API_KEY și opțional AI_MODEL/AI_BASE_URL.",
        }
    if AI_WEB_SEARCH and not _official_openai_base():
        return {
            "activ": False,
            "motiv": "AI_WEB_SEARCH=1 cere API-ul OpenAI oficial (AI_BASE_URL=https://api.openai.com/v1), "
                     "pentru ca răspunsul să includă citări web verificabile.",
        }
    with _lock_ai:
        return _analiza_locked(key)


def _analiza_locked(key):
    parti, fp = _amprenta_stare()
    veche = C.cache_get("analiza_ai", max_age=10 ** 9)
    declansator = "rulare manuală"

    def fetch():
        date_intrare = _digest()
        spec = _request_spec(date_intrare)
        body = json.dumps(spec["body"]).encode("utf-8")
        req = urllib.request.Request(
            spec["url"], data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            out = json.load(resp)
        if spec["mode"] == "web_cu_citari":
            text, citations, queries = _parse_responses_output(out)
        else:
            text = out["choices"][0]["message"]["content"].strip()
            citations, queries = [], []
        if not text:
            raise RuntimeError("API-ul AI nu a întors text")
        text, missing_headings = _normalize_response(text)
        rezultat = {
            "activ": True,
            "text": text,
            "model": out.get("model") or spec["model"],
            "mod": spec["mode"],
            "citari_web": citations,
            "cautari_web": queries,
            "sectiuni_lipsa": missing_headings,
            "generat": date.today().isoformat(),
            "declansator": declansator,
            "amprenta": fp,
            "amprenta_parti": parti,
            "prompt_version": PROMPT_VERSION,
            "prompt_sistem": PROMPT_SISTEM,
            "date_intrare": date_intrare,
        }
        C.daily_snapshot("analiza_ai", {
            "text": text, "model": rezultat["model"], "mod": spec["mode"],
            "citari_web": citations, "declansator": declansator,
        })
        return rezultat

    try:
        rezultat = fetch()
        C.cache_put("analiza_ai", rezultat, 10 ** 9)
        return {"data": rezultat, "stale": False}
    except Exception as exc:
        if veche:
            return {"data": veche["data"], "stale": True, "error": str(exc)}
        raise


if __name__ == "__main__":
    # Singurul punct de pornire intenționat al apelului AI. Interfața și
    # serverul HTTP nu apelează analiza cu run=True.
    print(json.dumps(analiza(run=True), ensure_ascii=False, indent=2))
