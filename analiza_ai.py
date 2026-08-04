"""Stratul opțional de interpretare AI — narativ, nu verdict.

Sinteza din capul paginii rămâne deterministă (calculată din date, fără
model de limbaj). Acest modul adaugă, separat și etichetat, o analiză
narativă generată de un LLM printr-un API OpenAI-compatibil, cu trei
reguli de transparență: promptul e public, datele de intrare sunt exact
digestul de mai jos (auditabil la /api/analiza-ai), iar modelul e obligat
să citeze cifrele pe care își sprijină fiecare afirmație.

Activare: AI_API_KEY=... (+ opțional AI_MODEL, AI_BASE_URL) în env-ul
serverului. Implicit: OpenAI, gpt-4o-mini. Merge identic cu OpenRouter,
Groq, Mistral sau un Ollama local (AI_BASE_URL=http://localhost:11434/v1).
"""

import hashlib
import json
import os
import urllib.request
from datetime import date

import anomalii
import connectors as C

# regenerare condusă de stare, nu de ceas:
MAX_VARSTA_S = 7 * 86400   # plasă de siguranță — reîmprospătare oricum la 7 zile
MIN_INTERVAL_S = 1800      # anti-flapping — nu mai des de o dată la 30 min

AI_BASE = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")

PROMPT_SISTEM = """Ești un analist hidrologic riguros și sobru. Primești un JSON cu starea la zi a Dunării: percentile climatologice pe secțiuni, bilanțuri, verificări încrucișate între surse independente (mire naționale, model Copernicus, satelit, gravimetrie GRACE) și statistici de precipitații.

Reguli stricte, nenegociabile:
1. Folosește EXCLUSIV cifrele din JSON. Nu inventa valori, stații, ani sau procente. Dacă o informație lipsește, spune că lipsește.
2. Structura obligatorie a răspunsului, cu exact aceste patru titluri:
   SITUAȚIA — 2-3 fraze, starea factuală.
   CAUZE PROBABILE — listă ordonată după plauzibilitate; fiecare cauză cu nivel de încredere (mare/medie/mică) și cu cifrele din JSON care o susțin, citate explicit.
   CE NU SE POATE CONCLUZIONA DIN ACESTE DATE — onestitate despre limite.
   CE AR SCHIMBA CONCLUZIA — ce valori sau evoluții viitoare ar contrazice interpretarea de mai sus.
3. Fără speculații politice, fără acuzații la adresa vreunei țări sau instituții. Dacă verificările încrucișate sunt în limite, spune explicit ce exclude asta.
4. Ton sobru, română, maximum 350 de cuvinte în total."""


def _digest():
    """Datele de intrare, distilate: tot ce contează, fără seriile lungi."""
    r = C.cached("anomalii_report", 6 * 3600, anomalii.report)["data"]
    st = C.cached("statistici", 6 * 3600, anomalii.full_stats)["data"]
    bi = C.cached("bilant_apa", 6 * 3600, anomalii.water_budget)["data"]

    def fara(d, *chei):
        return {k: v for k, v in (d or {}).items() if k not in chei}

    return {
        "data": date.today().isoformat(),
        "climatologie_sectiuni": [fara(c, "recent") for c in r.get("climatologie", [])],
        "bilant_portile_de_fier": r.get("bilant"),
        "inhga_vs_model": fara(r.get("masurat_vs_model"), "serie"),
        "mire_incrucisate": r.get("mire_crosscheck"),
        "precipitatii_vs_debit": r.get("precipitatii"),
        "satelit_altimetrie": r.get("satelit"),
        "germania_masurat_vs_model": r.get("germania"),
        "serbia_masurat_vs_model": r.get("serbia"),
        "austria_test_retentie": fara(r.get("austria"), "statii"),
        "bilant_apa_bazin_superior": bi,
        "statistici_precipitatii_zone": st.get("precipitatii"),
        "buletin_inhga": fara(_inhga(), "text_oficial"),
    }


def _inhga():
    try:
        return C.inhga_bulletin()["data"]
    except Exception:
        return {}


def _amprenta_stare():
    """Starea CATEGORIALĂ a fluviului — analiza se regenerează doar când se
    schimbă ceva de aici, nu la fiecare fluctuație de zecimale."""
    r = C.cached("anomalii_report", 6 * 3600, anomalii.report)["data"]
    bi = C.cached("bilant_apa", 6 * 3600, anomalii.water_budget)["data"]

    def z_ok(d, prag=1.5):
        return None if not d or d.get("insuficient") else abs(d.get("z", 0)) <= prag

    inhga = _inhga()
    debit, medie = inhga.get("debit_bazias_m3s"), inhga.get("media_multianuala_m3s")
    parti = {
        "severitati": [c.get("severitate") for c in r.get("climatologie", [])],
        "verificari": {
            "bilant_pf": z_ok(r.get("bilant")),
            "inhga_model": z_ok(r.get("masurat_vs_model")),
            "mire": (r.get("mire_crosscheck") or {}).get("mediana_abatere_cm", 99) <= 10,
            "satelit": (r.get("satelit") or {}).get("mediana_pct", 99) <= 15,
            "germania": (r.get("germania") or {}).get("coerent"),
            "serbia": (r.get("serbia") or {}).get("coerent"),
            "austria": not (r.get("austria") or {}).get("suspiciune_retentie", False),
        },
        # găleți de 5 puncte procentuale — trecerea dintr-una în alta = schimbare
        "bilant_gaura_pct5": round(100 * bi["bazias"]["lipsa_km3"]
                                   / bi["bazias"]["normal_km3"] / 5),
        "grace_luna": (bi.get("grace") or {}).get("luna"),
        # debitul INHGA în găleți de 10% din media multianuală
        "inhga_pct10": round(10 * debit / medie) if debit and medie else None,
    }
    fp = hashlib.sha256(json.dumps(parti, sort_keys=True).encode()).hexdigest()[:16]
    return parti, fp


def analiza():
    key = os.environ.get("AI_API_KEY", "").strip()
    if not key:
        return {"activ": False,
                "motiv": "Lipsește AI_API_KEY. Setați în env-ul serverului "
                         "AI_API_KEY (+ opțional AI_MODEL, AI_BASE_URL) pentru "
                         "analiza narativă generată de AI. Sinteza deterministă "
                         "din capul paginii funcționează oricum, fără AI."}

    parti, fp = _amprenta_stare()
    veche = C.cache_get("analiza_ai", max_age=10 ** 9)
    if veche:
        vd, varsta = veche["data"], veche["age"]
        neschimbat = vd.get("amprenta") == fp
        if (neschimbat and varsta < MAX_VARSTA_S) or varsta < MIN_INTERVAL_S:
            return {"data": vd, "stale": False}
        schimbate = [k for k in parti
                     if parti.get(k) != (vd.get("amprenta_parti") or {}).get(k)]
        declansator = (f"schimbare de stare: {', '.join(schimbate)}"
                       if not neschimbat else "reîmprospătare periodică (7 zile)")
    else:
        declansator = "prima analiză"

    def fetch():
        date_intrare = _digest()
        body = json.dumps({
            "model": AI_MODEL,
            "temperature": 0.2,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": PROMPT_SISTEM},
                {"role": "user", "content": "Datele de azi:\n" +
                 json.dumps(date_intrare, ensure_ascii=False)},
            ],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{AI_BASE}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            out = json.load(resp)
        text = out["choices"][0]["message"]["content"].strip()
        rezultat = {
            "activ": True, "text": text, "model": AI_MODEL,
            "generat": date.today().isoformat(),
            "declansator": declansator,
            "amprenta": fp, "amprenta_parti": parti,
            "prompt_sistem": PROMPT_SISTEM,
            "date_intrare": date_intrare,
        }
        C.daily_snapshot("analiza_ai", {"text": text, "model": AI_MODEL,
                                        "declansator": declansator})
        return rezultat

    try:
        rezultat = fetch()
        C.cache_put("analiza_ai", rezultat, 10 ** 9)
        return {"data": rezultat, "stale": False}
    except Exception as exc:
        if veche:  # la eroare de API servim ultima analiză, marcată
            return {"data": veche["data"], "stale": True, "error": str(exc)}
        raise
