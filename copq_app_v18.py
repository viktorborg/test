"""
COPQ-kalkylator – Micor AB  v10
Ändringar från v9:
- Dashboard är nu startsida med KPI:er (Total COPQ · DL totalt · NM totalt · Prefixanvändning)
- Analys-fliken börjar direkt från Fördelning per scenario (inga KPI:er)
- Ny flik Datakvalitet med datakvalitetsmätetal (prefix, klassificerade, manuella poster)
- NM = Nettomaterial – förtydligat med full term i hela UI:t
- Sidopanel borttagen – filuppladdning placerad längst upp på Dashboard (stöder flera filer)
- Möjlighet att ladda upp fler filer efter initial laddning borttagen
- Historik-fliken borttagen
- Avrundning till 1–2 decimaler genomgående
- Tydliga, explicita kolumnnamn i alla tabeller
- Ikoner borttagna ovanför rubriker inuti flikar (finns kvar i fliknavigationen)
"""

import streamlit as st
import pandas as pd
import io, re, json, os, math, base64, requests
from datetime import datetime
from copy import deepcopy

st.set_page_config(
    page_title="COPQ-kalkylator – Micor AB",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600;700&display=swap');
html,body,[class*="css"]{font-family:'IBM Plex Sans',sans-serif;}
/* Dölj sidopanel helt */
[data-testid="stSidebar"]{display:none!important;}
[data-testid="stSidebarCollapsedControl"]{display:none!important;}
.mc{background:#133273;border:1px solid #133273;border-radius:6px;padding:14px 18px;text-align:center;}
.ml{color:#6b8cae;font-size:10px;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:4px;}
.mv{color:#e8f4fd;font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;}
.mv.red{color:#ff4b4b;}.mv.green{color:#21c55d;}.mv.yellow{color:#f59e0b;}.mv.blue{color:#60a5fa;}
h1,h2,h3{font-family:'IBM Plex Sans',sans-serif!important;}
.warn{background:#2a1f00;border:1px solid #f59e0b;border-radius:4px;padding:6px 10px;font-size:12px;color:#f59e0b;}
</style>
""", unsafe_allow_html=True)

# ── Förkortningsdefinitioner (används genomgående i UI) ──────────────────────
# DL  = Direct Labor           (arbetstid × maskin-/personaltaxa per processteg)
# NM  = New Material       (material som förbrukas vid omarbetning: silver, stål, tänder, renskär)
# COPQ = Cost of Poor Quality = DL + NM

# ══════════════════════════════════════════════════════════════════════════════
# FILER & KONSTANTER
# ══════════════════════════════════════════════════════════════════════════════
CONFIG_FILE  = "copq_config.json"

# ══════════════════════════════════════════════════════════════════════════════
# GITHUB-SYNK
# Kräver GITHUB_TOKEN, GITHUB_REPO (och ev. GITHUB_BRANCH) i Streamlit Secrets.
# ══════════════════════════════════════════════════════════════════════════════
def _gh_enabled() -> bool:
    try:
        return bool(st.secrets.get("GITHUB_TOKEN") and st.secrets.get("GITHUB_REPO"))
    except Exception:
        return False

def _gh_headers() -> dict:
    return {"Authorization": f"token {st.secrets['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json"}

def _gh_branch() -> str:
    try:
        return st.secrets.get("GITHUB_BRANCH", "main")
    except Exception:
        return "main"

def github_push(filepath: str, content_str: str) -> tuple:
    if not _gh_enabled():
        return False, "GitHub-secrets saknas – synk inaktiverat."
    repo   = st.secrets["GITHUB_REPO"]
    branch = _gh_branch()
    url    = f"https://api.github.com/repos/{repo}/contents/{filepath}"
    headers = _gh_headers()
    r   = requests.get(url, headers=headers, params={"ref": branch}, timeout=10)
    sha = r.json().get("sha") if r.status_code == 200 else None
    payload = {
        "message": f"auto: uppdatera {filepath} [{datetime.now().strftime('%Y-%m-%d %H:%M')}]",
        "content": base64.b64encode(content_str.encode("utf-8")).decode(),
        "branch":  branch,
    }
    if sha:
        payload["sha"] = sha
    resp = requests.put(url, headers=headers, json=payload, timeout=15)
    if resp.status_code in (200, 201):
        return True, f"✅ {filepath} synkad till GitHub ({datetime.now().strftime('%H:%M:%S')})"
    return False, f"⚠️ GitHub-push misslyckades ({resp.status_code}): {resp.text[:120]}"

def github_pull(filepath: str):
    if not _gh_enabled():
        return None
    repo   = st.secrets["GITHUB_REPO"]
    branch = _gh_branch()
    url    = f"https://api.github.com/repos/{repo}/contents/{filepath}"
    r = requests.get(url, headers=_gh_headers(), params={"ref": branch}, timeout=10)
    if r.status_code == 200:
        return base64.b64decode(r.json()["content"]).decode("utf-8")
    return None

def restore_from_github_if_missing(filepath: str):
    if not os.path.exists(filepath):
        content = github_pull(filepath)
        if content:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

restore_from_github_if_missing(CONFIG_FILE)

# Kopiera logga till arbetskatalogen om den finns i uploads
import shutil as _shutil
if not os.path.exists("micor_logo.png"):
    for _logo_src in ["/mnt/user-data/uploads/micor_logo.png"]:
        if os.path.exists(_logo_src):
            _shutil.copy(_logo_src, "micor_logo.png")
            break

if "gh_status" not in st.session_state:
    st.session_state.gh_status = ""


# ══════════════════════════════════════════════════════════════════════════════
# STANDARDKONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
DEFAULT_STEPS = {
    "Laser + Gradning":             {"dl_rate": 3620, "tid_h": 0.011519},
    "Blästring tandsäte & Tvätt":  {"dl_rate": 1034, "tid_h": 0.006664},
    "Anlöp":                        {"dl_rate":  350, "tid_h": 0.111100},
    "Planslipning":                 {"dl_rate": 2207, "tid_h": 0.150675,
                                     "diameter_ranges": [
                                         {"min": 100, "max": 200, "tid_h": 0.045},
                                         {"min": 201, "max": 300, "tid_h": 0.030},
                                         {"min": 301, "max": 400, "tid_h": 0.040},
                                         {"min": 401, "max": 600, "tid_h": 0.044},
                                         {"min": 601, "max": 800, "tid_h": 0.120},
                                     ]},
    "I: Planrikt & Sträck":        {"dl_rate":  300, "tid_h": 0.100695},
    "Kontrollrikt":                 {"dl_rate":  300, "tid_h": 0.079611},
    "Fräsning Centrumhål":         {"dl_rate":  831, "tid_h": 0.021944},
    "Lödning":                      {"dl_rate":  397, "tid_h": 0.113704},
    "Borstning-Automat":            {"dl_rate":  784, "tid_h": 0.032400},
    "Tvätt - 2":                    {"dl_rate":  661, "tid_h": 0.066600},
    "Lödavsyning - VisionL":       {"dl_rate":  208, "tid_h": 0.046200},
    "Rensskärslöd + Förflussning": {"dl_rate":  315, "tid_h": 0.038889},
    "II: Planrikt & Puts":         {"dl_rate":  300, "tid_h": 0.100695},
    "Sidslip Renskär":             {"dl_rate":  313, "tid_h": 0.058333},
    "HM-Slip Automat":             {"dl_rate":  584, "tid_h": 0.170556},
    "Putsning":                     {"dl_rate": 1340, "tid_h": 0.012663},
    "Slipavsyning":                 {"dl_rate":  961, "tid_h": 0.023000},
    "Slutavsyning":                 {"dl_rate":  961, "tid_h": 0.038467},
    "Handlödning":                  {"dl_rate": 3419, "tid_h": 0.0},
    "HM-Slip Manuell":             {"dl_rate":  584, "tid_h": 0.0},
}
DEFAULT_EXTRA_STEPS = {
    "Bortlödning tänder":  {"dl_rate": 3419, "tid_h": 0.00278},
    "Bortlödning renskär": {"dl_rate": 3419, "tid_h": 0.004167},
}
DEFAULT_SCENARIO_STEPS = {
    "S1a": ["Laser + Gradning","Blästring tandsäte & Tvätt","Anlöp","Planslipning",
            "I: Planrikt & Sträck","Kontrollrikt","Fräsning Centrumhål","Lödning",
            "Borstning-Automat","Tvätt - 2","Lödavsyning - VisionL",
            "Rensskärslöd + Förflussning","II: Planrikt & Puts","Sidslip Renskär",
            "HM-Slip Automat","Putsning","Slipavsyning","Slutavsyning"],
    "S1b": ["Laser + Gradning","Blästring tandsäte & Tvätt","Anlöp","Planslipning",
            "I: Planrikt & Sträck","Kontrollrikt","Fräsning Centrumhål","Lödning",
            "Borstning-Automat","Tvätt - 2","Lödavsyning - VisionL",
            "Rensskärslöd + Förflussning","II: Planrikt & Puts","Sidslip Renskär",
            "HM-Slip Automat","Putsning","Slipavsyning","Slutavsyning"],
    "S2a": ["Lödning","Borstning-Automat","Tvätt - 2","Lödavsyning - VisionL",
            "HM-Slip Automat","Putsning","Slipavsyning","Slutavsyning"],
    "S2b": ["Lödning","Borstning-Automat","Tvätt - 2","Lödavsyning - VisionL",
            "II: Planrikt & Puts","HM-Slip Automat","Putsning","Slipavsyning","Slutavsyning"],
    "S3":  ["Rensskärslöd + Förflussning"],
    "S4":  ["Lödning","Borstning-Automat","Tvätt - 2","Lödavsyning - VisionL"],
    "S5":  ["Laser + Gradning","Blästring tandsäte & Tvätt","Anlöp","Planslipning",
            "I: Planrikt & Sträck","Kontrollrikt","Fräsning Centrumhål","Lödning",
            "Borstning-Automat","Tvätt - 2","Lödavsyning - VisionL"],
    "S6a": ["Fräsning Centrumhål"],
    "S6b": ["I: Planrikt & Sträck"],
    "S6c": ["Laser + Gradning","Blästring tandsäte & Tvätt","Anlöp","Planslipning",
            "I: Planrikt & Sträck","Kontrollrikt","Fräsning Centrumhål"],
}
DEFAULT_SCENARIO_NM = {
    "S1a": {"stal": True}, "S1b": {},
    "S2a": {"silver": True, "bortlodning_tander": True},
    "S2b": {"silver": True, "bortlodning_tander": True},
    "S3":  {"renskar": True, "bortlodning_renskar": True},
    "S4":  {}, "S5":  {},
    "S6a": {}, "S6b": {},
    "S6c": {"stal": True},
}
DEFAULT_SCENARIO_INFO = {
    "S1a": "Repor – Slipavslut",
    "S1b": "Repor – Övriga",
    "S2a": "Felaktiga tänder ≤3 st",
    "S2b": "Felaktiga tänder >3 st",
    "S3":  "Felaktiga renskär",
    "S4":  "Vision-fel",
    "S5":  "Spånvinkel – laser/stomme",
    "S6a": "Centrumhål för litet",
    "S6b": "Centrumhål för stort (omarbetas)",
    "S6c": "Centrumhål för stort (kasseras)",
}
DEFAULT_MATERIALS = {
    "silver_kr_kg":    5500.0,
    "tand_kr_st":         5.0,
    "silver_kg_tand":  0.001,
    "renskar_kr_st":    20.0,
    "silver_kg_renskar": 0.002,
    "stal_kr_kg":       30.0,
    "stal_densitet":  7850.0,
}

def default_config():
    return {
        "steps":          deepcopy(DEFAULT_STEPS),
        "extra_steps":    deepcopy(DEFAULT_EXTRA_STEPS),
        "scenario_steps": deepcopy(DEFAULT_SCENARIO_STEPS),
        "scenario_nm":    deepcopy(DEFAULT_SCENARIO_NM),
        "scenario_info":  deepcopy(DEFAULT_SCENARIO_INFO),
        "materials":      deepcopy(DEFAULT_MATERIALS),
    }

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            cfg = default_config()
            for k in cfg:
                if k in saved:
                    if isinstance(cfg[k], dict):
                        cfg[k].update(saved[k])
                    else:
                        cfg[k] = saved[k]
            for steg, default_vals in DEFAULT_STEPS.items():
                if steg in cfg["steps"]:
                    for field, val in default_vals.items():
                        if field not in cfg["steps"][steg]:
                            cfg["steps"][steg][field] = val
            return cfg
        except Exception:
            pass
    return default_config()

def save_config(cfg):
    content = json.dumps(cfg, indent=2, ensure_ascii=False)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    ok, msg = github_push(CONFIG_FILE, content)
    st.session_state.gh_status = msg

if "cfg" not in st.session_state:
    st.session_state.cfg = load_config()
cfg = st.session_state.cfg

# ══════════════════════════════════════════════════════════════════════════════
# FORMATERING – mellanrum som tusenavskiljare
# ══════════════════════════════════════════════════════════════════════════════
def fmtkr(x, dec=0):
    """Formaterar ett tal med mellanrum (narrow no-break space) som tusenavskiljare, t.ex. 12 345 kr."""
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return "–"
        return f"{float(x):,.{dec}f}".replace(",", "\u202f") + " kr"
    except Exception:
        return "–"

def fmtnum(x, dec=0, suffix=""):
    """Som fmtkr men utan kr-suffix, används i tabellceller utan enhet."""
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return "–"
        return f"{float(x):,.{dec}f}".replace(",", "\u202f") + suffix
    except Exception:
        return "–"

# ══════════════════════════════════════════════════════════════════════════════
# BERÄKNINGSFUNKTIONER
# ══════════════════════════════════════════════════════════════════════════════
def parse_artikel(ben):
    if not ben or str(ben).strip() in ("nan", "NaN", ""):
        return None, None
    s = str(ben).replace(",", ".")
    try:
        dm = re.search(r'-(\d+\.?\d*)', s)
        sm = re.search(r'/(\d+\.?\d*)', s)
        return (float(dm.group(1)) if dm else None), (float(sm.group(1)) if sm else None)
    except:
        return None, None

def stalvikt_kg(diam, stomme, dens):
    if not diam or not stomme:
        return None
    return math.pi * ((diam / 1000) / 2) ** 2 * (stomme / 1000) * dens

def planslip_tid(diam, step_cfg):
    for r in step_cfg.get("diameter_ranges", []):
        if diam and r["min"] <= diam <= r["max"]:
            return r["tid_h"]
    return step_cfg.get("tid_h") or 0

def berakna_dl(sc, diam, cfg, custom_steps=None):
    sc_steps = custom_steps if custom_steps is not None else cfg["scenario_steps"].get(sc or "", [])
    total, breakdown = 0.0, {}
    for steg in sc_steps:
        s    = cfg["steps"].get(steg, {})
        rate = s.get("dl_rate") or 0
        tid  = planslip_tid(diam, s) if steg == "Planslipning" else (s.get("tid_h") or 0)
        kr   = rate * tid
        total += kr
        breakdown[steg] = round(kr, 4)
    return total, breakdown

def berakna_nm(sc, tand_tot, diam, stomme, cfg, klingor=1):
    """
    Calculates New Material cost (NM).
    tand_tot = TOTALT antal tänder (ej per klinga).
    Silver/renskär baseras på totalt antal. Stål baseras på vikt × klingor.
    """
    mat = cfg["materials"]
    nm  = cfg["scenario_nm"].get(sc or "", {})
    cost, breakdown, missing = 0.0, {}, False

    if nm.get("silver"):
        if tand_tot:
            kr_silver = tand_tot * mat["silver_kg_tand"] * mat["silver_kr_kg"]
            kr_tand   = tand_tot * mat["tand_kr_st"]
            kr = kr_silver + kr_tand
            cost += kr
            breakdown["Silver (tänder)"] = round(kr_silver, 2)
            breakdown["Tand (ny)"]       = round(kr_tand,   2)
        else:
            missing = True

    if nm.get("renskar"):
        if tand_tot:
            kr_silver = tand_tot * mat["silver_kg_renskar"] * mat["silver_kr_kg"]
            kr_rensk  = tand_tot * mat["renskar_kr_st"]
            kr = kr_silver + kr_rensk
            cost += kr
            breakdown["Silver (renskär)"] = round(kr_silver, 2)
            breakdown["Renskär (ny)"]     = round(kr_rensk,  2)
        else:
            missing = True

    if nm.get("stal"):
        kg = stalvikt_kg(diam, stomme, mat["stal_densitet"])
        if kg:
            kr = kg * mat["stal_kr_kg"] * klingor
            cost += kr
            breakdown["Stål"] = round(kr, 2)
            breakdown["_kg"]  = round(kg, 4)

    if nm.get("bortlodning_tander"):
        es = cfg["extra_steps"].get("Bortlödning tänder", {})
        kr_per_enhet = (es.get("dl_rate") or 0) * (es.get("tid_h") or 0)
        if tand_tot:
            kr = kr_per_enhet * tand_tot
            cost += kr
            breakdown["Bortlödning tänder"] = round(kr, 2)
        else:
            missing = True

    if nm.get("bortlodning_renskar"):
        es = cfg["extra_steps"].get("Bortlödning renskär", {})
        kr_per_enhet = (es.get("dl_rate") or 0) * (es.get("tid_h") or 0)
        if tand_tot:
            kr = kr_per_enhet * tand_tot
            cost += kr
            breakdown["Bortlödning renskär"] = round(kr, 2)
        else:
            missing = True

    return cost, breakdown, missing

def scenario_formel(sc, cfg, dl, nm_brk, tand_tot, klingor, diam, stomme):
    mat     = cfg["materials"]
    nm_cfg  = cfg["scenario_nm"].get(sc or "", {})
    steg    = cfg["scenario_steps"].get(sc or "", [])
    steg_str = " + ".join(steg) if steg else "–"
    parts = [f"DL = ({steg_str}) × {klingor} kl = {dl*klingor:.2f} kr"]
    nm_parts = []
    if nm_cfg.get("silver"):
        if tand_tot:
            kr_s = nm_brk.get("Silver (tänder)", 0)
            kr_t = nm_brk.get("Tand (ny)", 0)
            nm_parts.append(
                f"Tänder: {tand_tot} st × ({mat['silver_kg_tand']} kg × {mat['silver_kr_kg']:.0f} kr/kg silver"
                f" + {mat['tand_kr_st']:.2f} kr/tand) = {kr_s:.2f} + {kr_t:.2f} = {kr_s+kr_t:.2f} kr"
            )
        else:
            nm_parts.append("Tänder: tandantal saknas")
    if nm_cfg.get("renskar"):
        if tand_tot:
            kr_s = nm_brk.get("Silver (renskär)", 0)
            kr_r = nm_brk.get("Renskär (ny)", 0)
            nm_parts.append(
                f"Renskär: {tand_tot} st × ({mat['silver_kg_renskar']} kg × {mat['silver_kr_kg']:.0f} kr/kg silver"
                f" + {mat['renskar_kr_st']:.2f} kr/st) = {kr_s:.2f} + {kr_r:.2f} = {kr_s+kr_r:.2f} kr"
            )
        else:
            nm_parts.append("Renskär: antal saknas")
    if nm_cfg.get("stal"):
        kg = stalvikt_kg(diam, stomme, mat["stal_densitet"])
        if kg:
            nm_parts.append(
                f"Stål: π×(d/2)²×stomme×dens×{klingor}kl = {kg*klingor:.4f} kg × {mat['stal_kr_kg']:.2f} kr/kg"
                f" = {nm_brk.get('Stål',0):.2f} kr"
            )
    if nm_cfg.get("bortlodning_tander"):
        es  = cfg["extra_steps"].get("Bortlödning tänder", {})
        kr_e = (es.get("dl_rate") or 0) * (es.get("tid_h") or 0)
        if tand_tot:
            nm_parts.append(f"Bortlödning tänder: {tand_tot} tänder(tot) × {kr_e:.4f} kr/tand = {nm_brk.get('Bortlödning tänder',0):.2f} kr")
        else:
            nm_parts.append("Bortlödning tänder: tandantal saknas")
    if nm_cfg.get("bortlodning_renskar"):
        es  = cfg["extra_steps"].get("Bortlödning renskär", {})
        kr_e = (es.get("dl_rate") or 0) * (es.get("tid_h") or 0)
        if tand_tot:
            nm_parts.append(f"Bortlödning renskär: {tand_tot} renskär(tot) × {kr_e:.4f} kr/st = {nm_brk.get('Bortlödning renskär',0):.2f} kr")
        else:
            nm_parts.append("Bortlödning renskär: antal saknas")
    if nm_parts:
        parts.append("NM (New Material) = " + " + ".join(nm_parts))
    return "\n".join(parts)

def berakna_rad_copq(sc, kl, tand_tot, diam, stomme, cfg, custom_steps=None):
    dl, dl_brk = berakna_dl(sc, diam, cfg, custom_steps)
    nm, nm_brk, miss = berakna_nm(sc, tand_tot, diam, stomme, cfg, klingor=kl)
    dl_tot = dl * kl
    total  = dl_tot + nm
    nm_per_kl = nm / kl if kl else nm
    return {
        "dl": dl, "nm": nm_per_kl, "nm_tot": nm, "dl_tot": dl_tot,
        "total_per_kl": dl + nm_per_kl, "total": total,
        "dl_brk": dl_brk, "nm_brk": nm_brk, "missing_tand": miss,
    }

# ══════════════════════════════════════════════════════════════════════════════
# KLASSIFICERING
# ══════════════════════════════════════════════════════════════════════════════
def parse_prefix(ft, valid):
    """
    Format: S2b:10 → scenario S2b, 10 tänder TOTALT (siffra avser ej per klinga).
    Äldre format S2b10 stöds också.
    """
    if not ft:
        return None, None
    m = re.match(r'^\s*(S\d[a-zA-Z]?):?(\d*)', str(ft).strip(), re.IGNORECASE)
    if m:
        raw = m.group(1)
        kod = raw[0].upper() + raw[1] + (raw[2].lower() if len(raw) > 2 else "")
        if kod in valid:
            tal = m.group(2)
            return kod, int(tal) if tal else None
    return None, None

def klassificera_text(kass, ft, felantal):
    f = str(ft).lower()
    if kass == 14 and "rens" in f:
        return "S3"
    if kass == 14:
        return "S2a" if felantal <= 3 else "S2b"
    if any(o in f for o in ["c-hål", "c hål", "centrumhål", "centrum"]):
        if any(o in f for o in ["för litet", "trång", "trånga", "tajt", "tajta", "förlitet"]):
            return "S6a"
        if any(o in f for o in ["stort", "för stort", "stora"]):
            return "S6c" if any(o in f for o in ["skrot", "skrotas", "kasser", "kass"]) else "S6b"
    if "spånvinkel" in f and "stomme" in f:
        return "S5"
    if "vision" in f:
        return "S4"
    if "repa" in f:
        return "S1a" if "slipavslut" in f else "S1b"
    return None

def klassificera(row, valid):
    ft      = str(row.get("Felbeskrivning", ""))
    kass    = row.get("Kass.kod")
    felantal = int(pd.to_numeric(row.get("Felantal", 1), errors="coerce") or 1)
    sc, tand = parse_prefix(ft, valid)
    if sc:
        if sc in ("S2a", "S2b") and tand is not None:
            sc = "S2a" if tand <= 3 else "S2b"
        return sc, tand, "prefix"
    sc = klassificera_text(kass, ft, felantal)
    return sc, None, ("text" if sc else None)

# ══════════════════════════════════════════════════════════════════════════════
# EXCEL-EXPORT
# ══════════════════════════════════════════════════════════════════════════════
def bygg_excel(df, cfg):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = "COPQ Detaljerad"
    hf  = PatternFill("solid", fgColor="133273")
    hfnt = Font(color="DDEEFF", bold=True, name="Calibri", size=10)
    alt  = PatternFill("solid", fgColor="E8F0F8")
    okl  = PatternFill("solid", fgColor="FFF3CD")
    man  = PatternFill("solid", fgColor="EDE7F6")
    brd  = Border(bottom=Side(style="thin", color="B0C4DE"),
                  right=Side(style="thin", color="B0C4DE"))
    hdrs = [
        "Ärende", "Artikelnummer", "Artikelbenämning", "Antal klingor",
        "Tänder (tot)", "Diameter (mm)", "Stomme (mm)",
        "Stålvikt (kg)", "Stålkostnad (kr)", "Felbeskrivning",
        "Kassationsbenämning", "Kass.kod", "Ärendedatum",
        "Scenario", "Klassificeringskälla", "Scenariobeskrivning",
        "Direct labor per klinga (kr)", "New material per klinga (kr)",
        "COPQ per klinga (kr)", "Total COPQ (kr)",
    ]
    wids = [10, 13, 26, 9, 9, 10, 9, 10, 10, 38, 22, 9, 12, 10, 8, 26, 18, 18, 14, 14]
    for c, (h, w) in enumerate(zip(hdrs, wids), 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = hfnt; cell.fill = hf
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = brd
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.row_dimensions[1].height = 28

    def fkr(x):
        try:
            return round(float(x), 2) if pd.notna(x) else ""
        except:
            return ""

    for r, (_, row) in enumerate(df.iterrows(), 2):
        sc    = row.get("Scenario", "")
        ok    = sc and str(sc) not in ("None", "nan", "")
        kalla = row.get("Kalla", "")
        fill  = (okl if not ok else man if kalla == "manuell" else (alt if r % 2 == 0 else PatternFill()))
        tand  = row.get("Tandantal")
        tv    = "" if (tand is None or (isinstance(tand, float) and pd.isna(tand))) else int(tand)
        vals  = [
            row.get("Ärende", ""), row.get("Artikelnummer", ""), row.get("Artikelbenämning", ""),
            row.get("Felantal", ""), tv,
            row.get("Diameter_mm", ""), row.get("Stomme_mm", ""),
            fkr(row.get("Stal_kg")), fkr(row.get("Stal_kr")),
            row.get("Felbeskrivning", ""), row.get("Kassationsbenämning", ""), row.get("Kass.kod", ""),
            str(row.get("Ärendedatum", ""))[:10] if pd.notna(row.get("Ärendedatum")) else "",
            sc if ok else "", kalla if ok else "",
            cfg["scenario_info"].get(sc, "") if ok else "",
            fkr(row.get("DL_per_klinga")), fkr(row.get("NM_per_klinga")),
            fkr(row.get("COPQ_per_klinga")), fkr(row.get("Total_COPQ")),
        ]
        for c, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.font = Font(name="Calibri", size=9)
            cell.fill = fill; cell.border = brd
            if c in (17, 18, 19, 20) and ok:
                cell.number_format = '#,##0.00 "kr"'
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:T{len(df)+1}"

    # Sammanfattningsblad
    ws2 = wb.create_sheet("Sammanfattning")
    for col, w in zip("ABCDEFGH", [12, 30, 10, 10, 18, 18, 15, 10]):
        ws2.column_dimensions[col].width = w
    ws2["A1"] = "COPQ Sammanfattning – Micor AB"
    ws2["A1"].font = Font(name="Calibri", size=14, bold=True, color="0F1923")
    ws2["A2"] = f"Genererad: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ws2["A2"].font = Font(name="Calibri", size=9, color="888888")
    for c, h in enumerate(
        ["Scenario", "Beskrivning", "Ärenden", "Klingor",
         "Direct Labor DL (kr)", "New Material NM (kr)", "Total COPQ (kr)", "Andel (%)"], 1
    ):
        cell = ws2.cell(row=4, column=c, value=h)
        cell.font = hfnt; cell.fill = hf
        cell.alignment = Alignment(horizontal="center"); cell.border = brd
    cl = df[df["Scenario"].notna() & (df["Scenario"] != "")].copy()
    if not cl.empty:
        cl["_dl"] = cl["DL_tot"].fillna(0)
        cl["_nm"] = cl["NM_tot"].fillna(0)
        summ = (cl.groupby("Scenario")
                .agg(Ar=("Ärende", "count"), Kl=("Felantal", "sum"),
                     DL=("_dl", "sum"), NM=("_nm", "sum"), COPQ=("Total_COPQ", "sum"))
                .reset_index())
        tot = summ["COPQ"].sum()
        summ["Andel"] = summ["COPQ"] / tot * 100 if tot else 0
        summ["Beskrivning"] = summ["Scenario"].map(cfg["scenario_info"])
        for r, (_, row) in enumerate(summ.iterrows(), 5):
            fill = alt if r % 2 == 0 else PatternFill()
            for c, val in enumerate(
                [row["Scenario"], row["Beskrivning"], row["Ar"], row["Kl"],
                 row["DL"], row["NM"], row["COPQ"], round(row["Andel"], 1)], 1
            ):
                cell = ws2.cell(row=r, column=c, value=val)
                cell.font = Font(name="Calibri", size=9)
                cell.fill = fill; cell.border = brd
                if c in (5, 6, 7): cell.number_format = '#,##0.00 "kr"'
                if c == 8:         cell.number_format = '0.0"%"'
        tr = len(summ) + 5
        ws2.cell(row=tr, column=1, value="TOTALT").font = Font(name="Calibri", bold=True)
        tc = ws2.cell(row=tr, column=7, value=tot)
        tc.font = Font(name="Calibri", bold=True, color="C00000")
        tc.number_format = '#,##0.00 "kr"'
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf

# ══════════════════════════════════════════════════════════════════════════════
# SESSION STATE & ANALYSBERÄKNING
# ══════════════════════════════════════════════════════════════════════════════
if "raw_df"    not in st.session_state: st.session_state.raw_df    = None
if "file_names" not in st.session_state: st.session_state.file_names = []
if "manuell"   not in st.session_state: st.session_state.manuell   = {}

def compute_analysis(raw_df, cfg, manuell):
    """Klassificerar, beräknar DL/NM/COPQ och lägger till alla beräknade kolumner."""
    df = raw_df.copy()
    df["Kass.kod"] = pd.to_numeric(df["Kass.kod"], errors="coerce")
    df["Felantal"] = pd.to_numeric(df["Felantal"], errors="coerce").fillna(1).astype(int)

    valid_sc = set(cfg["scenario_info"].keys())
    klass = df.apply(lambda r: klassificera(r, valid_sc), axis=1)
    df["Scenario"] = klass.map(lambda x: x[0])
    df["Tandantal"] = klass.map(lambda x: x[1])
    df["Kalla"]     = klass.map(lambda x: x[2])

    parsed = df["Artikelbenämning"].map(parse_artikel)
    df["Diameter_mm"] = parsed.map(lambda x: x[0])
    df["Stomme_mm"]   = parsed.map(lambda x: x[1])
    df["Stal_kg"] = df.apply(
        lambda r: stalvikt_kg(r["Diameter_mm"], r["Stomme_mm"], cfg["materials"]["stal_densitet"]), axis=1
    )
    df["Stal_kr"] = df["Stal_kg"].map(
        lambda x: x * cfg["materials"]["stal_kr_kg"] if x is not None else None
    )

    def calc_row(row):
        idx    = row.name
        sc     = row.get("Scenario")
        man    = manuell.get(idx)
        custom = None
        if man:
            sc     = man.get("sc") or sc
            custom = man.get("custom_steps")
        if not sc or str(sc) in ("None", "nan", ""):
            return pd.Series({
                "Scenario": None, "Kalla": None,
                "DL_per_klinga": None, "NM_per_klinga": None,
                "COPQ_per_klinga": None, "Total_COPQ": None,
                "DL_tot": None, "NM_tot": None, "Missing_tand": False,
            })
        kl   = int(row.get("Felantal") or 1)
        tand = row.get("Tandantal")
        tand = int(tand) if (tand is not None and not (isinstance(tand, float) and pd.isna(tand))) else None
        res  = berakna_rad_copq(sc, kl, tand, row.get("Diameter_mm"), row.get("Stomme_mm"), cfg, custom)
        return pd.Series({
            "Scenario":       sc,
            "Kalla":          "manuell" if man else row.get("Kalla"),
            "DL_per_klinga":  res["dl"],
            "NM_per_klinga":  res["nm"],
            "COPQ_per_klinga": res["total_per_kl"],
            "Total_COPQ":     res["total"],
            "DL_tot":         res["dl_tot"],
            "NM_tot":         res["nm_tot"],
            "Missing_tand":   res["missing_tand"],
        })

    res_df = df.apply(calc_row, axis=1)
    for col in ["Scenario", "Kalla", "DL_per_klinga", "NM_per_klinga",
                "COPQ_per_klinga", "Total_COPQ", "DL_tot", "NM_tot", "Missing_tand"]:
        df[col] = res_df[col]
    return df

# ══════════════════════════════════════════════════════════════════════════════
# SIDHUVUD + FILUPPLADDNING
# ══════════════════════════════════════════════════════════════════════════════
import base64 as _b64

def _logo_b64() -> str:
    """Returnerar base64-kodad logga som data-URI."""
    try:
        with open("micor_logo.png", "rb") as f:
            return _b64.b64encode(f.read()).decode()
    except Exception:
        return ""

_LOGO_DATA = _logo_b64()

def _logo_html(height: int = 48) -> str:
    if _LOGO_DATA:
        pad = int(height * 0.18)
        return (
            f'<span style="display:inline-flex;align-items:center;justify-content:center;'
            f'background:#133273;border-radius:10px;padding:{pad}px {pad+4}px;'
            f'margin-left:14px;vertical-align:middle;">'
            f'<img src="data:image/png;base64,{_LOGO_DATA}" '
            f'style="height:{height}px;display:block;">'
            f'</span>'
        )
    return ""

st.markdown(
    f'<h1 style="display:flex;align-items:center;gap:0;">COPQ &nbsp;{_logo_html(90)}</h1>',
    unsafe_allow_html=True,
)

# Initiera analysvariabler
df = None
n_tot = n_kl = n_pref = n_man = n_ok = n_miss = 0
tot_copq = tot_dl = tot_nm = pref_pct = 0.0

if st.session_state.raw_df is None:
    # ── Filuppladdning (visas bara en gång, försvinner när data är inläst) ──
    st.markdown("Ladda upp en eller flera internavvikelse-Excel-filer för att starta analysen.")
    uploaded_files = st.file_uploader(
        "Excel-filer (.xlsx, .xls)",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )
    if uploaded_files:
        raw_dfs, errors = [], []
        for uf in uploaded_files:
            try:
                xls = pd.read_excel(uf, sheet_name=None)
                preferred = ["sorterad data", "Indata", "Data"]
                sheet = next((p for p in preferred if p in xls), list(xls.keys())[0])
                df_i = xls[sheet].copy()
                df_i.columns = [str(c).strip() for c in df_i.columns]
                if str(df_i.iloc[0, 0]).strip() == "Ärende":
                    df_i = df_i.iloc[1:].reset_index(drop=True)
                miss_col = [c for c in ["Felbeskrivning", "Kass.kod", "Felantal"] if c not in df_i.columns]
                if miss_col:
                    errors.append(f"**{uf.name}**: Saknar kolumner {', '.join(miss_col)}")
                else:
                    raw_dfs.append(df_i)
            except Exception as e:
                errors.append(f"**{uf.name}**: {e}")
        for err in errors:
            st.error(err)
        if raw_dfs:
            combined = pd.concat(raw_dfs, ignore_index=True)
            st.session_state.raw_df     = combined
            st.session_state.file_names = [f.name for f in uploaded_files]
            st.session_state.manuell    = {}
            st.rerun()
    # Rapporteringsstandard (visas på välkomstsidan)
    with st.expander("Rapporteringsstandard – prefixformat"):
        st.markdown("""
| Format | Tolkning |
|--------|----------|
| `S6a text…` | Scenario S6a |
| `S2a:5` | S2a, **5 tänder totalt** |
| `S2b:10` | S2b, **10 tänder totalt** |
| `S3:4` | S3, **4 renskär totalt** |

Siffran anger **totalt antal tänder** (ej per klinga).  
Direct Labor (DL) beräknas per klinga × antal klingor.  
New Material (NM) för silver/renskär beräknas på det totala antalet.  
Utan siffra för S2/S3 kan NM (New Material)-kostnaden ej beräknas.
""")
    st.stop()
else:
    # ── Data inläst – beräkna analys ─────────────────────────────────────────
    df        = compute_analysis(st.session_state.raw_df, cfg, st.session_state.manuell)
    n_tot     = len(df)
    n_kl      = int(df["Scenario"].notna().sum())
    n_pref    = int((df["Kalla"] == "prefix").sum())
    n_man     = int((df["Kalla"] == "manuell").sum())
    n_ok      = n_tot - n_kl
    n_miss    = int(df["Missing_tand"].sum())
    tot_copq  = round(df["Total_COPQ"].fillna(0).sum(), 2)
    tot_dl    = round(df["DL_tot"].fillna(0).sum(), 2)
    tot_nm    = round(df["NM_tot"].fillna(0).sum(), 2)
    pref_pct  = round(n_pref / n_kl * 100, 1) if n_kl > 0 else 0.0
    klass_pct = round(n_kl / n_tot * 100, 1) if n_tot > 0 else 0.0
    cap_col, reset_col = st.columns([8, 1])
    with cap_col:
        st.caption(f"Inläst från: {', '.join(st.session_state.file_names)}  ·  {n_tot} ärenden")
    with reset_col:
        if st.button("🗑 Rensa", help="Ta bort inläst data och börja om"):
            st.session_state.raw_df = None
            st.session_state.file_names = []
            st.session_state.manuell = {}
            st.rerun()

    with st.expander("➕ Ladda upp fler Excel-filer"):
        extra_files = st.file_uploader(
            "Lägg till fler internavvikelse-Excel-filer",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
            key="extra_upload",
            label_visibility="collapsed",
        )
        if extra_files:
            extra_dfs, extra_errors = [], []
            for uf in extra_files:
                if uf.name in st.session_state.file_names:
                    extra_errors.append(f"**{uf.name}**: redan inläst, hoppas över.")
                    continue
                try:
                    xls = pd.read_excel(uf, sheet_name=None)
                    preferred = ["sorterad data", "Indata", "Data"]
                    sheet = next((p for p in preferred if p in xls), list(xls.keys())[0])
                    df_i = xls[sheet].copy()
                    df_i.columns = [str(c).strip() for c in df_i.columns]
                    if str(df_i.iloc[0, 0]).strip() == "Ärende":
                        df_i = df_i.iloc[1:].reset_index(drop=True)
                    miss_col = [c for c in ["Felbeskrivning", "Kass.kod", "Felantal"] if c not in df_i.columns]
                    if miss_col:
                        extra_errors.append(f"**{uf.name}**: Saknar kolumner {', '.join(miss_col)}")
                    else:
                        extra_dfs.append(df_i)
                        st.session_state.file_names.append(uf.name)
                except Exception as e:
                    extra_errors.append(f"**{uf.name}**: {e}")
            for err in extra_errors:
                st.warning(err)
            if extra_dfs:
                combined = pd.concat([st.session_state.raw_df] + extra_dfs, ignore_index=True)
                st.session_state.raw_df = combined
                st.session_state.manuell = {}
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# FLIKAR
# ══════════════════════════════════════════════════════════════════════════════
tab_dash, tab_analys, tab_dkv, tab_sc, tab_steg, tab_mat, tab_ny = st.tabs([
    "📊 Dashboard",
    "🔍 Analys",
    "📋 Datakvalitet",
    "🗂 Scenarion & NM",
    "⚙️ Processteg",
    "🪙 Materialpriser",
    "➕ Nytt scenario",
])

# ══════════════════════════════════════════════════════════════════════════════
# MATERIALPRISER
# ══════════════════════════════════════════════════════════════════════════════
with tab_mat:
    st.markdown("### Materialpriser & spotpriser")
    mat = cfg["materials"]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Stål")
        mat["stal_kr_kg"]    = st.number_input("Spotpris stål (kr/kg)",  value=float(mat["stal_kr_kg"]),    min_value=0.0, step=1.0,   format="%.2f")
        mat["stal_densitet"] = st.number_input("Densitet stål (kg/m³)",  value=float(mat["stal_densitet"]), min_value=0.0, step=10.0,  format="%.0f")
        st.info("V = π × (d/2)² × stomme\nkg = V × densitet\nkr = kg × spotpris")
    with c2:
        st.markdown("#### Silver")
        mat["silver_kr_kg"] = st.number_input("Spotpris silver (kr/kg)", value=float(mat["silver_kr_kg"]), min_value=0.0, step=100.0, format="%.2f")
    st.markdown("#### Tänder (ny tand)")
    tc1, tc2 = st.columns(2)
    with tc1:
        mat["tand_kr_st"]     = st.number_input("Kostnad ny tand (kr/st)",   value=float(mat.get("tand_kr_st", 5.0)),       min_value=0.0, step=0.5,    format="%.2f")
        mat["silver_kg_tand"] = st.number_input("Silver per tand (kg/tand)", value=float(mat.get("silver_kg_tand", 0.001)), min_value=0.0, step=0.0001, format="%.4f")
    with tc2:
        kr_tand        = mat["tand_kr_st"] + mat["silver_kg_tand"] * mat["silver_kr_kg"]
        kr_tand_silver = mat["silver_kg_tand"] * mat["silver_kr_kg"]
        st.success(f"→ **{kr_tand:.2f} kr per tand** ({mat['tand_kr_st']:.2f} kr del + {kr_tand_silver:.2f} kr silver)")
    st.markdown("#### Renskär (nytt renskär)")
    rc1, rc2 = st.columns(2)
    with rc1:
        mat["renskar_kr_st"]     = st.number_input("Kostnad nytt renskär (kr/st)", value=float(mat.get("renskar_kr_st", 20.0)),      min_value=0.0, step=1.0,    format="%.2f")
        mat["silver_kg_renskar"] = st.number_input("Silver per renskär (kg/st)",   value=float(mat.get("silver_kg_renskar", 0.002)), min_value=0.0, step=0.0001, format="%.4f")
    with rc2:
        kr_rensk        = mat["renskar_kr_st"] + mat["silver_kg_renskar"] * mat["silver_kr_kg"]
        kr_rensk_silver = mat["silver_kg_renskar"] * mat["silver_kr_kg"]
        st.success(f"→ **{kr_rensk:.2f} kr per renskär** ({mat['renskar_kr_st']:.2f} kr del + {kr_rensk_silver:.2f} kr silver)")
    cfg["materials"] = mat
    st.divider()
    if st.button("Spara materialpriser", type="primary"):
        save_config(cfg)
        st.success(f"Sparat till `{CONFIG_FILE}`")

# ══════════════════════════════════════════════════════════════════════════════
# PROCESSTEG
# ══════════════════════════════════════════════════════════════════════════════
with tab_steg:
    st.markdown("### Processteg – Direct Labor (DL)")
    steps  = cfg["steps"]
    edited = {}
    for i in range(0, len(steps), 2):
        cols2 = st.columns(2)
        for j, (steg, s) in enumerate(list(steps.items())[i:i+2]):
            with cols2[j]:
                with st.expander(f"**{steg}**"):
                    rate = st.number_input("DL-taxa (kr/h)", value=float(s.get("dl_rate") or 0), min_value=0.0, step=10.0, format="%.0f", key=f"sr_{steg}")
                    if steg == "Planslipning":
                        st.markdown("**Stycktid per diameterrange (h):**")
                        ranges     = list(s.get("diameter_ranges", []))
                        new_ranges = []
                        for ri, r in enumerate(ranges):
                            rc = st.columns([2, 2, 3, 1])
                            with rc[0]: rmin = st.number_input("Min mm", value=int(r["min"]), step=50, key=f"rmin_{ri}")
                            with rc[1]: rmax = st.number_input("Max mm", value=int(r["max"]), step=50, key=f"rmax_{ri}")
                            with rc[2]: rtid = st.number_input("tid (h)", value=float(r["tid_h"]), step=0.001, format="%.4f", key=f"rtid_{ri}")
                            with rc[3]:
                                st.markdown("<br>", unsafe_allow_html=True)
                                if not st.button("✕", key=f"rdel_{ri}"):
                                    new_ranges.append({"min": rmin, "max": rmax, "tid_h": rtid})
                        if st.button("+ Range", key="add_range"):
                            new_ranges.append({"min": 0, "max": 999, "tid_h": 0.0})
                        tid = st.number_input("Fallback tid (h)", value=float(s.get("tid_h") or 0), min_value=0.0, step=0.001, format="%.6f", key=f"st_{steg}")
                        edited[steg] = {"dl_rate": rate, "tid_h": tid, "diameter_ranges": new_ranges}
                    else:
                        tid = st.number_input("Tid/klinga (h)", value=float(s.get("tid_h") or 0), min_value=0.0, step=0.001, format="%.6f", key=f"st_{steg}")
                        st.caption(f"→ **{rate*tid:.4f} kr/klinga**")
                        edited[steg] = {"dl_rate": rate, "tid_h": tid}
    cfg["steps"] = edited

    st.markdown("#### Extra DL-steg")
    for steg, s in cfg["extra_steps"].items():
        with st.expander(f"**{steg}**"):
            r2 = st.number_input("kr/h",   value=float(s.get("dl_rate") or 0), step=10.0,  key=f"er_{steg}")
            t2 = st.number_input("tid (h)", value=float(s.get("tid_h") or 0),   step=0.001, format="%.4f", key=f"et_{steg}")
            cfg["extra_steps"][steg] = {"dl_rate": r2, "tid_h": t2}

    st.markdown("**Lägg till nytt processteg:**")
    st.caption("Ange namn, DL-taxa (kr/h) och snittstycktid (h/klinga).")
    nc = st.columns([3, 2, 2, 1])
    with nc[0]:
        st.caption("Stegnamn")
        ns_name = st.text_input("Namn", placeholder="Stegnamn", label_visibility="collapsed", key="ns_name")
    with nc[1]:
        st.caption("DL-taxa (kr/h)")
        ns_rate = st.number_input("kr/h", min_value=0.0, step=10.0, key="ns_rate", label_visibility="collapsed")
    with nc[2]:
        st.caption("Snittstycktid (h/kl)")
        ns_tid  = st.number_input("h/kl", min_value=0.0, step=0.001, format="%.4f", key="ns_tid", label_visibility="collapsed")
    with nc[3]:
        if st.button("Lägg till", key="add_step"):
            if ns_name and ns_name not in cfg["steps"]:
                cfg["steps"][ns_name] = {"dl_rate": ns_rate, "tid_h": ns_tid}
                st.rerun()
    st.divider()
    if st.button("Spara processteg", type="primary"):
        save_config(cfg)
        st.success(f"Sparat till `{CONFIG_FILE}`")

# ══════════════════════════════════════════════════════════════════════════════
# SCENARION & NETTOMATERIAL (NM)
# ══════════════════════════════════════════════════════════════════════════════
with tab_sc:
    st.markdown("### Scenarion – namn, processteg och New Material (NM)")
    all_step_names = list(cfg["steps"].keys())
    for sc_key in list(cfg["scenario_info"].keys()):
        info   = cfg["scenario_info"].get(sc_key, "")
        dl_ex, _ = berakna_dl(sc_key, None, cfg)
        with st.expander(f"**{sc_key}** – {info}  ·  DL ≈ {dl_ex:.0f} kr/kl"):
            cn, cc = st.columns([4, 1])
            with cn:
                new_info = st.text_input("Namn/beskrivning", value=info, key=f"si_{sc_key}")
                cfg["scenario_info"][sc_key] = new_info
            with cc:
                st.markdown(f"<br><code>{sc_key}</code>", unsafe_allow_html=True)

            st.markdown("**DL-steg (✓ = aktivt):**")
            active     = cfg["scenario_steps"].get(sc_key, [])
            new_active = []
            sc_cols    = st.columns(3)
            for i, steg in enumerate(all_step_names):
                with sc_cols[i % 3]:
                    if st.checkbox(steg, value=(steg in active), key=f"ck_{sc_key}_{steg}"):
                        new_active.append(steg)
            cfg["scenario_steps"][sc_key] = new_active

            st.markdown("**New Material (NM) – material & extra-DL:**")
            nm     = cfg["scenario_nm"].get(sc_key, {})
            mc     = st.columns(5)
            with mc[0]: nm["silver"]             = st.checkbox("Silver (per tand)",    value=nm.get("silver", False),             key=f"nm_s_{sc_key}")
            with mc[1]: nm["renskar"]            = st.checkbox("Renskär (per st)",     value=nm.get("renskar", False),            key=f"nm_r_{sc_key}")
            with mc[2]: nm["stal"]               = st.checkbox("Stål (kasseras)",      value=nm.get("stal", False),               key=f"nm_k_{sc_key}")
            with mc[3]: nm["bortlodning_tander"] = st.checkbox("Bortlödning tänder",   value=nm.get("bortlodning_tander", False), key=f"nm_bt_{sc_key}")
            with mc[4]: nm["bortlodning_renskar"]= st.checkbox("Bortlödning renskär",  value=nm.get("bortlodning_renskar", False),key=f"nm_br_{sc_key}")
            cfg["scenario_nm"][sc_key] = nm

            if new_active:
                dl_rows = [
                    {"Steg": s,
                     "DL-taxa (kr/h)": cfg["steps"].get(s, {}).get("dl_rate", 0),
                     "Stycktid (h)":   cfg["steps"].get(s, {}).get("tid_h", 0),
                     "Kostnad per klinga (kr)": round(
                         (cfg["steps"].get(s, {}).get("dl_rate") or 0) *
                         (cfg["steps"].get(s, {}).get("tid_h")  or 0), 4
                     )}
                    for s in new_active
                ]
                st.dataframe(pd.DataFrame(dl_rows), hide_index=True, use_container_width=True)
                dl_sc, _ = berakna_dl(sc_key, None, cfg)
                nm_cfg_sc = cfg["scenario_nm"].get(sc_key, {})
                nm_parts  = []
                if nm_cfg_sc.get("silver"):
                    mat_sc = cfg["materials"]
                    kr_t   = mat_sc["tand_kr_st"] + mat_sc["silver_kg_tand"] * mat_sc["silver_kr_kg"]
                    nm_parts.append(f"Tänder: N st × ({mat_sc['silver_kg_tand']} kg×{mat_sc['silver_kr_kg']:.0f} kr/kg silver + {mat_sc['tand_kr_st']:.2f} kr/tand) = {kr_t:.2f} kr/tand")
                if nm_cfg_sc.get("renskar"):
                    mat_sc = cfg["materials"]
                    kr_r   = mat_sc["renskar_kr_st"] + mat_sc["silver_kg_renskar"] * mat_sc["silver_kr_kg"]
                    nm_parts.append(f"Renskär: N st × ({mat_sc['silver_kg_renskar']} kg×{mat_sc['silver_kr_kg']:.0f} kr/kg silver + {mat_sc['renskar_kr_st']:.2f} kr/st) = {kr_r:.2f} kr/renskär")
                if nm_cfg_sc.get("stal"):
                    nm_parts.append(f"Stål: π×(d/2)²×stomme×ρ×kl × {cfg['materials']['stal_kr_kg']:.2f} kr/kg")
                if nm_cfg_sc.get("bortlodning_tander"):
                    es   = cfg["extra_steps"].get("Bortlödning tänder", {})
                    kr_e = (es.get("dl_rate") or 0) * (es.get("tid_h") or 0)
                    nm_parts.append(f"Bortlödning tänder: N tänder(tot) × {kr_e:.4f} kr/tand")
                if nm_cfg_sc.get("bortlodning_renskar"):
                    es   = cfg["extra_steps"].get("Bortlödning renskär", {})
                    kr_e = (es.get("dl_rate") or 0) * (es.get("tid_h") or 0)
                    nm_parts.append(f"Bortlödning renskär: N renskär(tot) × {kr_e:.4f} kr/st")
                formel_parts = [f"DL = ({' + '.join(new_active)}) per klinga = {dl_sc:.4f} kr/kl"]
                if nm_parts:
                    formel_parts.append("NM (New Material) = " + " + ".join(nm_parts))
                formel_parts.append("COPQ = DL × klingor + NM")
                with st.expander("Formel för detta scenario"):
                    st.code("\n".join(formel_parts), language=None)
    st.divider()
    if st.button("Spara scenarion", type="primary"):
        save_config(cfg)
        st.success(f"Sparat till `{CONFIG_FILE}`")

# ══════════════════════════════════════════════════════════════════════════════
# NYTT SCENARIO
# ══════════════════════════════════════════════════════════════════════════════
with tab_ny:
    st.markdown("### Lägg till nytt scenario")
    c1, c2 = st.columns(2)
    with c1:
        ny_kod  = st.text_input("Kod (t.ex. S7a)")
        ny_info = st.text_input("Beskrivning")
    with c2:
        ny_silver   = st.checkbox("Silver",             key="ny_s")
        ny_renskar  = st.checkbox("Renskär",            key="ny_r")
        ny_stal     = st.checkbox("Stål",               key="ny_k")
        ny_bl_tand  = st.checkbox("Bortlödning tänder", key="ny_bt")
        ny_bl_rensk = st.checkbox("Bortlödning renskär",key="ny_br")

    st.markdown("**Välj processteg som ingår i scenariot:**")
    all_step_names_ny = list(cfg["steps"].keys())
    ny_chosen_steps   = []
    step_cols_ny      = st.columns(3)
    for si, sn in enumerate(all_step_names_ny):
        with step_cols_ny[si % 3]:
            if st.checkbox(sn, value=False, key=f"ny_step_{si}"):
                ny_chosen_steps.append(sn)
    if ny_chosen_steps:
        dl_preview, _ = berakna_dl(None, None, cfg, ny_chosen_steps)
        st.info(f"DL med valda steg: **{dl_preview:.0f} kr/klinga**")
    if st.button("Skapa scenario", type="primary"):
        if ny_kod and ny_kod not in cfg["scenario_info"]:
            cfg["scenario_info"][ny_kod]  = ny_info
            cfg["scenario_steps"][ny_kod] = ny_chosen_steps
            cfg["scenario_nm"][ny_kod]    = {
                "silver": ny_silver, "renskar": ny_renskar, "stal": ny_stal,
                "bortlodning_tander": ny_bl_tand, "bortlodning_renskar": ny_bl_rensk,
            }
            save_config(cfg)
            st.success(f"**{ny_kod}** skapat med {len(ny_chosen_steps)} steg!")
            st.rerun()
        elif ny_kod in cfg["scenario_info"]:
            st.warning("Scenariokod finns redan.")
        else:
            st.error("Ange en scenariokod.")

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD – KPI:er + diagram (startsida)
# ══════════════════════════════════════════════════════════════════════════════
with tab_dash:
    import plotly.express as px
    import plotly.graph_objects as go

    # ── KPI-rad: TOTAL COPQ | DL totalt | NM totalt | Prefixanvändning ────────
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(
            f'<div class="mc"><div class="ml">Total COPQ</div>'
            f'<div class="mv red">{fmtnum(tot_copq)} kr</div></div>',
            unsafe_allow_html=True)
    with k2:
        st.markdown(
            f'<div class="mc"><div class="ml">Direct Labor (DL) totalt</div>'
            f'<div class="mv blue">{fmtnum(tot_dl)} kr</div></div>',
            unsafe_allow_html=True)
    with k3:
        st.markdown(
            f'<div class="mc"><div class="ml">New Material (NM) totalt</div>'
            f'<div class="mv yellow">{fmtnum(tot_nm)} kr</div></div>',
            unsafe_allow_html=True)
    with k4:
        st.markdown(
            f'<div class="mc"><div class="ml">Prefixanvändning</div>'
            f'<div class="mv green">{pref_pct:.1f}%</div></div>',
            unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Snabbdiagram: scenario-fördelning + DL/NM-fördelning ─────────────────
    q1, q2 = st.columns(2)
    PLOT_BASE = dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="white",
        font=dict(color="#133273"),
        xaxis=dict(gridcolor="#cccccc", linecolor="#333333", zerolinecolor="#333333"),
        yaxis=dict(gridcolor="#cccccc", linecolor="#333333", zerolinecolor="#333333"),
        legend=dict(orientation="h"), height=440,
        separators="\u202f.",
    )
    _DARK_BLUE = "#133273"
    _MID_BLUE  = "#1e4fa8"

    with q1:
        if "Scenario" in df.columns:
            sc_sum = (df[df["Scenario"].notna()]
                      .groupby("Scenario")["Total_COPQ"].sum()
                      .reset_index()
                      .sort_values("Total_COPQ", ascending=False))
            sc_sum.columns = ["Scenario", "Total COPQ (kr)"]
            fig1 = px.bar(
                sc_sum, x="Scenario", y="Total COPQ (kr)",
                title="Total COPQ per scenario",
                color_discrete_sequence=[_DARK_BLUE],
            )
            fig1.update_layout(**PLOT_BASE)
            fig1.update_yaxes(ticksuffix=" kr")
            st.plotly_chart(fig1, use_container_width=True)

    with q2:
        if tot_dl + tot_nm > 0:
            pie_df = pd.DataFrame({
                "Kostnadstyp": ["Direct Labor (DL)", "New Material (NM)"],
                "Kostnad (kr)": [round(tot_dl, 2), round(tot_nm, 2)],
            })
            fig2 = px.pie(
                pie_df, names="Kostnadstyp", values="Kostnad (kr)",
                title="Cost breakdown DL / NM",
                hole=0.35,
                color_discrete_map={
                    "Direct Labor (DL)":   _DARK_BLUE,
                    "New Material (NM)": _MID_BLUE,
                },
            )
            fig2.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="white",
                               font=dict(color="#133273"),
                               legend=dict(orientation="h"), height=440)
            st.plotly_chart(fig2, use_container_width=True)

    # ── Graf-byggare (aktuell sessions data) ─────────────────────────────────
    if df is not None and not df.empty:
        st.divider()
        st.markdown("### Graf-byggare")
        st.caption("Bygg egna diagram baserade på den uppladdade datan.")

        NUM_LABELS_S = {
            "Total_COPQ":    "Total COPQ (kr)",
            "DL_tot":        "Direct Labor DL totalt (kr)",
            "NM_tot":        "New Material NM totalt (kr)",
            "DL_per_klinga": "DL per klinga (kr)",
            "NM_per_klinga": "NM per klinga (kr)",
            "Felantal":      "Antal klingor",
        }
        CAT_LABELS_S = {
            "Scenario": "Scenario",
            "Kalla": "Klassificeringskälla",
            "Artikelbenämning": "Artikelbenämning",
            "_datum_period": "Tid (år-månad)",
            "_datum_dag": "Tid (dag)",  # ← ny rad
            "Diameter_mm": "Diameter (mm)",
        }
        AGG_OPTS_S   = {"sum": "Summa", "mean": "Medelvärde", "count": "Antal ärenden", "median": "Median"}
        CHART_TYPES_S = ["Stapeldiagram", "Linjediagram", "Cirkeldiagram", "Punktdiagram", "Boxplot"]

        # Förbered hjälpkolumner för tid och diameter
        fdf_s = df.copy()
        if "Ärendedatum" in fdf_s.columns:
            fdf_s["_datum_period"] = pd.to_datetime(fdf_s["Ärendedatum"], errors="coerce").dt.to_period("M").astype(str)
            fdf_s["_datum_dag"] = pd.to_datetime(fdf_s["Ärendedatum"], errors="coerce").dt.strftime("%Y-%m-%d")
        else:
            fdf_s["_datum_period"] = None
            fdf_s["_datum_dag"] = None

        NUM_COLS_S = {c: c for c in NUM_LABELS_S if c in fdf_s.columns}
        CAT_COLS_S = {c: c for c in CAT_LABELS_S if c in fdf_s.columns}

        with st.expander("Filter (valfritt)", expanded=False):
            # ── Tidshorisont ──────────────────────────────────────────────────
            has_datum = "Ärendedatum" in fdf_s.columns and fdf_s["_datum_dag"].notna().any()
            if has_datum:
                st.markdown("**Tidshorisont**")
                _all_dates = pd.to_datetime(fdf_s["_datum_dag"], errors="coerce").dropna()
                _min_date  = _all_dates.min().date()
                _max_date  = _all_dates.max().date()

                th_col1, th_col2 = st.columns([2, 3])
                with th_col1:
                    preset = st.selectbox(
                        "Snabbval",
                        ["Hela perioden", "Senaste 30 dagarna", "Senaste 90 dagarna",
                         "Senaste 6 månaderna", "Senaste 12 månaderna", "Anpassat intervall"],
                        key="s_gb_preset",
                    )
                with th_col2:
                    from datetime import date, timedelta
                    _today = date.today()
                    if preset == "Senaste 30 dagarna":
                        _default_from, _default_to = _today - timedelta(days=30), _today
                    elif preset == "Senaste 90 dagarna":
                        _default_from, _default_to = _today - timedelta(days=90), _today
                    elif preset == "Senaste 6 månaderna":
                        _default_from, _default_to = _today - timedelta(days=183), _today
                    elif preset == "Senaste 12 månaderna":
                        _default_from, _default_to = _today - timedelta(days=365), _today
                    else:
                        _default_from, _default_to = _min_date, _max_date

                    # Kläm till datumets faktiska spann
                    _default_from = max(_default_from, _min_date)
                    _default_to   = min(_default_to,   _max_date)

                    if preset == "Anpassat intervall":
                        dc1, dc2 = st.columns(2)
                        with dc1:
                            date_from = st.date_input("Från", value=_default_from,
                                                      min_value=_min_date, max_value=_max_date,
                                                      key="s_gb_date_from")
                        with dc2:
                            date_to = st.date_input("Till", value=_default_to,
                                                    min_value=_min_date, max_value=_max_date,
                                                    key="s_gb_date_to")
                    else:
                        date_from, date_to = _default_from, _default_to
                        st.caption(f"Visar **{date_from}** → **{date_to}**")

                st.divider()
            else:
                date_from = date_to = None

            # ── Övriga filter ─────────────────────────────────────────────────
            sf1, sf2 = st.columns(2)
            all_sc_s = sorted(df["Scenario"].dropna().unique().tolist()) if "Scenario" in df.columns else []
            with sf1: sel_sc_s = st.multiselect("Scenarion", all_sc_s, default=all_sc_s, key="s_gb_sc")
            with sf2: only_cl_s = st.checkbox("Bara klassificerade", value=False, key="s_gb_only_cl")

        # Applicera tidsfilter
        if has_datum and date_from and date_to:
            _dates = pd.to_datetime(fdf_s["_datum_dag"], errors="coerce").dt.date
            fdf_s = fdf_s[(_dates >= date_from) & (_dates <= date_to)]

        if all_sc_s and sel_sc_s: fdf_s = fdf_s[fdf_s["Scenario"].isin(sel_sc_s)]
        if only_cl_s and "Scenario" in fdf_s.columns: fdf_s = fdf_s[fdf_s["Scenario"].notna()]

        sg1, sg2, sg3, sg4, sg5 = st.columns([2, 2, 2, 2, 2])
        with sg1: chart_type_s = st.selectbox("Graftyp", CHART_TYPES_S, key="s_gb_type")
        with sg2:
            x_lbl_s = st.selectbox("X-axel / Grupp", [CAT_LABELS_S[c] for c in CAT_COLS_S if c in fdf_s.columns], key="s_gb_x")
            x_col_s = [k for k, v in CAT_LABELS_S.items() if v == x_lbl_s and k in fdf_s.columns][0]
        with sg3:
            y_lbl_s = st.selectbox("Y-axel / Värde", [NUM_LABELS_S[c] for c in NUM_COLS_S if c in fdf_s.columns], key="s_gb_y")
            y_col_s = [k for k, v in NUM_LABELS_S.items() if v == y_lbl_s and k in fdf_s.columns][0]
        with sg4:
            if chart_type_s in ("Stapeldiagram", "Linjediagram"):
                agg_lbl_s = st.selectbox("Aggregering", list(AGG_OPTS_S.values()), key="s_gb_agg")
                agg_fn_s  = [k for k, v in AGG_OPTS_S.items() if v == agg_lbl_s][0]
            elif chart_type_s == "Cirkeldiagram":
                agg_lbl_s = "Summa"; agg_fn_s = "sum"
            else:
                agg_fn_s = None
        with sg5:
            color_s_opts = ["(ingen)"] + [CAT_LABELS_S[c] for c in CAT_COLS_S if c in fdf_s.columns and c != x_col_s]
            color_lbl_s = st.selectbox("Färgdimension", color_s_opts, key="s_gb_color")
            color_col_s = None if color_lbl_s == "(ingen)" else \
                [k for k, v in CAT_LABELS_S.items() if v == color_lbl_s and k in fdf_s.columns][0]

        try:
            # Bestäm visningsnamn för x-axeln
            x_display_s = CAT_LABELS_S.get(x_col_s, x_col_s)

            if chart_type_s in ("Punktdiagram", "Boxplot"):
                plot_df_s = fdf_s.dropna(subset=[x_col_s, y_col_s])
            elif agg_fn_s == "count":
                grp_s = [x_col_s] + ([color_col_s] if color_col_s else [])
                plot_df_s = fdf_s.groupby(grp_s)[y_col_s].count().reset_index()
            else:
                grp_s = [x_col_s] + ([color_col_s] if color_col_s else [])
                plot_df_s = fdf_s.dropna(subset=[x_col_s]).groupby(grp_s)[y_col_s].agg(agg_fn_s).reset_index()

            # Sortera på x-axeln om det är tidsperiod eller diameter
            if x_col_s in ("_datum_period", "_datum_dag", "Diameter_mm"):
                plot_df_s = plot_df_s.sort_values(x_col_s)

            title_s = f"{y_lbl_s} per {x_display_s}"
            ysfx_s  = " kr" if "kr" in y_lbl_s.lower() else ""

            # Byt kolumnnamn till displaynamn för axeln
            plot_df_s = plot_df_s.rename(columns={x_col_s: x_display_s})
            if color_col_s:
                color_display_s = CAT_LABELS_S.get(color_col_s, color_col_s)
                plot_df_s = plot_df_s.rename(columns={color_col_s: color_display_s})
            else:
                color_display_s = None

            if chart_type_s == "Stapeldiagram":
                fig_s = px.bar(plot_df_s, x=x_display_s, y=y_col_s, color=color_display_s, title=title_s,
                               barmode="group" if color_display_s else "relative",
                               color_discrete_sequence=[_DARK_BLUE, _MID_BLUE, "#3b82f6", "#60a5fa"])
                fig_s.update_layout(**PLOT_BASE); fig_s.update_yaxes(ticksuffix=ysfx_s)
            elif chart_type_s == "Linjediagram":
                fig_s = px.line(plot_df_s, x=x_display_s, y=y_col_s, color=color_display_s, title=title_s,
                                markers=True, color_discrete_sequence=[_DARK_BLUE, _MID_BLUE, "#3b82f6"])
                fig_s.update_layout(**PLOT_BASE); fig_s.update_yaxes(ticksuffix=ysfx_s)
            elif chart_type_s == "Cirkeldiagram":
                pie_s = fdf_s.dropna(subset=[x_col_s]).groupby(x_col_s)[y_col_s].sum().reset_index()
                pie_s = pie_s.rename(columns={x_col_s: x_display_s})
                fig_s = px.pie(pie_s, names=x_display_s, values=y_col_s, title=title_s, hole=0.35,
                               color_discrete_sequence=[_DARK_BLUE, _MID_BLUE, "#3b82f6", "#60a5fa", "#93c5fd"])
                fig_s.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="white",
                                    font=dict(color="#133273"),
                                    legend=dict(orientation="h"), height=420)
            elif chart_type_s == "Punktdiagram":
                fig_s = px.scatter(plot_df_s, x=x_display_s, y=y_col_s, color=color_display_s, title=title_s,
                                   size_max=16, color_discrete_sequence=[_DARK_BLUE, _MID_BLUE, "#3b82f6"])
                fig_s.update_layout(**PLOT_BASE); fig_s.update_yaxes(ticksuffix=ysfx_s)
            elif chart_type_s == "Boxplot":
                fig_s = px.box(plot_df_s, x=x_display_s, y=y_col_s, color=color_display_s, title=title_s,
                               color_discrete_sequence=[_DARK_BLUE, _MID_BLUE, "#3b82f6"])
                fig_s.update_layout(**PLOT_BASE); fig_s.update_yaxes(ticksuffix=ysfx_s)
            st.plotly_chart(fig_s, use_container_width=True)
            with st.expander("Visa underliggande data"):
                st.dataframe(plot_df_s.round(2), hide_index=True, use_container_width=True)
        except Exception as e:
            st.warning(f"Kunde inte rita grafen: {e}")

    # ── GitHub-synk (komprimerat) ─────────────────────────────────────────────
    if _gh_enabled():
        st.divider()
        with st.expander("GitHub-synk"):
            if st.session_state.gh_status:
                if "✅" in st.session_state.gh_status:
                    st.success(st.session_state.gh_status)
                else:
                    st.warning(st.session_state.gh_status)
            else:
                st.info("Synk aktiv – config pushas automatiskt vid sparning.")
            if st.button("Synka config", use_container_width=True, key="manual_push_cfg"):
                content = json.dumps(st.session_state.get("cfg", default_config()), indent=2, ensure_ascii=False)
                ok, msg = github_push(CONFIG_FILE, content)
                st.session_state.gh_status = msg; st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# ANALYS – börjar från Fördelning per scenario (inga KPI:er)
# ══════════════════════════════════════════════════════════════════════════════
with tab_analys:
    if n_miss > 0:
        st.markdown(
            f'<div class="warn">⚠️ {n_miss} ärenden saknar tandantal – '
            f'New Material (NM)-kostnaden kan ej beräknas. '
            f'Använd prefix t.ex. <code>S2b:10</code> eller manuell klassificering.</div>',
            unsafe_allow_html=True)
        st.markdown("")

    # ── Fördelning per scenario ───────────────────────────────────────────────
    st.markdown("### Fördelning per scenario")
    cl = df[df["Scenario"].notna()].copy()
    if not cl.empty:
        cl["_dl"] = cl["DL_tot"].fillna(0)
        cl["_nm"] = cl["NM_tot"].fillna(0)
        summ = (
            cl.groupby("Scenario").agg(
                Ärenden=("Ärende", "count"),
                Klingor=("Felantal", "sum"),
                DL=("_dl", "sum"),
                NM=("_nm", "sum"),
                COPQ=("Total_COPQ", "sum"),
            )
            .reset_index()
            .sort_values("COPQ", ascending=False)
        )
        summ["Scenariobeskrivning"] = summ["Scenario"].map(cfg["scenario_info"])
        summ["Andel (%)"]           = (summ["COPQ"] / tot_copq * 100).round(1).astype(str) + " %"
        for c in ["DL", "NM", "COPQ"]:
            summ[f"{c}_fmt"] = summ[c].map(lambda x: fmtnum(x))
        display_summ = summ[[
            "Scenario", "Scenariobeskrivning", "Ärenden", "Klingor",
            "DL_fmt", "NM_fmt", "COPQ_fmt", "Andel (%)",
        ]].copy()
        display_summ.columns = [
            "Scenario", "Beskrivning", "Antal ärenden", "Antal klingor",
            "Direct Labor DL (kr)", "New Material NM (kr)", "Total COPQ (kr)", "Andel (%)",
        ]
        st.dataframe(display_summ, hide_index=True, use_container_width=True)
        st.caption(
            "DL = Direct Labor (arbetstid × taxa per processteg)  ·  "
            "NM = New Material (silver, stål, tänder, renskär som förbrukas)"
        )
    else:
        st.info("Inga klassificerade ärenden i den uppladdade filen.")

    # ── Alla ärenden ──────────────────────────────────────────────────────────
    st.markdown("### Alla ärenden")
    f1, f2, f3, f4 = st.columns(4)
    with f1: visa     = st.selectbox("Visa", ["Alla", "Klassificerade", "Oklassificerade", "Manuella", "Saknar tandantal"])
    with f2: valt_sc  = st.selectbox("Scenario", ["Alla"] + sorted(df["Scenario"].dropna().unique().tolist()))
    with f3: kalla_f  = st.selectbox("Klassificeringskälla", ["Alla", "prefix", "text", "manuell"])
    with f4: fritext  = st.text_input("Sök felbeskrivning", placeholder="centrum, repa…")

    dv = df.copy()
    if visa == "Klassificerade":       dv = dv[dv["Scenario"].notna()]
    elif visa == "Oklassificerade":    dv = dv[dv["Scenario"].isna()]
    elif visa == "Manuella":           dv = dv[dv["Kalla"] == "manuell"]
    elif visa == "Saknar tandantal":   dv = dv[dv["Missing_tand"] == True]
    if valt_sc != "Alla":              dv = dv[dv["Scenario"] == valt_sc]
    if kalla_f != "Alla":              dv = dv[dv["Kalla"] == kalla_f]
    if fritext:
        dv = dv[dv["Felbeskrivning"].astype(str).str.lower().str.contains(fritext.lower(), na=False)]

    disp = dv[[
        "Ärende", "Artikelbenämning", "Felantal",
        "Stal_kr", "Tandantal", "Felbeskrivning", "Scenario",
        "DL_per_klinga", "NM_per_klinga", "Total_COPQ", "Missing_tand",
    ]].copy()

    def fmtx(x, fmt=None):
        if fmt == "kg":
            try:
                return f"{float(x):.2f} kg" if pd.notna(x) else "–"
            except Exception:
                return "–"
        return fmtkr(x)

    disp["Stal_kr"]        = disp["Stal_kr"].map(fmtx)
    disp["DL_per_klinga"]  = disp["DL_per_klinga"].map(fmtx)
    disp["NM_per_klinga"]  = disp["NM_per_klinga"].map(fmtx)
    disp["Total_COPQ"]     = disp["Total_COPQ"].map(fmtx)
    disp["Tandantal"]      = disp["Tandantal"].map(
        lambda x: "–" if (x is None or (isinstance(x, float) and pd.isna(x))) else int(x)
    )
    disp["Scenario"]       = disp["Scenario"].fillna("–")
    disp["⚠️"]             = disp["Missing_tand"].map(lambda x: "⚠️" if x else "")
    disp                   = disp.drop(columns=["Missing_tand"])
    disp.columns = [
        "Ärende", "Artikelbenämning", "Antal klingor",
        "Stålkostnad (kr)", "Antal tänder (tot)", "Felbeskrivning",
        "Scenario", "Direct labor per klinga (kr)",
        "New material per klinga (kr)", "Total COPQ (kr)", "⚠️",
    ]
    st.dataframe(disp, use_container_width=True, hide_index=True, height=380)
    st.caption(
        f"Visar {len(dv)} av {n_tot}  ·  "
        "⚠️ = New Material (NM) ej beräknad eftersom tandantal saknas i felbeskrivningen"
    )

    # ── Manuell klassificering ────────────────────────────────────────────────
    oklass_df = df[df["Scenario"].isna()].copy()
    if not oklass_df.empty:
        st.markdown("### Manuell klassificering")
        st.caption(f"{len(oklass_df)} oklassificerade ärenden – välj scenario manuellt nedan.")
        sc_opts = ["(ingen)"] + sorted(cfg["scenario_info"].keys()) + ["[ Anpassade steg ]"]

        for idx, row in oklass_df.iterrows():
            with st.expander(
                f"**{row.get('Ärende', idx)}**  "
                f"{str(row.get('Artikelbenämning', ''))[:30]}  ·  "
                f"*{str(row.get('Felbeskrivning', ''))[:55]}*"
            ):
                man_st     = st.session_state.manuell.get(idx, {})
                cur_sc     = man_st.get("sc", "(ingen)") if man_st else "(ingen)"
                has_custom = man_st.get("custom_steps") is not None if man_st else False

                sel        = st.selectbox("Scenario", sc_opts,
                                          index=sc_opts.index(cur_sc) if cur_sc in sc_opts else 0,
                                          key=f"msc_{idx}")
                use_custom = sel == "[ Anpassade steg ]" or has_custom

                if use_custom:
                    st.markdown("**Välj processteg som ingår i omarbetet:**")
                    cur_custom = man_st.get("custom_steps", []) if man_st else []
                    chosen     = []
                    step_c     = st.columns(3)
                    for si, sn in enumerate(list(cfg["steps"].keys())):
                        with step_c[si % 3]:
                            if st.checkbox(sn, value=(sn in cur_custom), key=f"cs_{idx}_{si}"):
                                chosen.append(sn)
                    dl_custom, _ = berakna_dl(None, row.get("Diameter_mm"), cfg, chosen)
                    st.info(
                        f"DL med valda steg: **{dl_custom:.0f} kr/klinga** "
                        f"× {row.get('Felantal', 1)} klingor = "
                        f"**{dl_custom * int(row.get('Felantal', 1)):.0f} kr**"
                    )
                    sc_for_save = sel if sel not in ("[ Anpassade steg ]", "(ingen)") else None
                    st.session_state.manuell[idx] = {"sc": sc_for_save, "custom_steps": chosen}

                elif sel != "(ingen)":
                    kl_ex  = int(row.get("Felantal", 1))
                    dl_ex, dl_brk_ex = berakna_dl(sel, row.get("Diameter_mm"), cfg)
                    tand_ex = row.get("Tandantal")
                    tand_ex = int(tand_ex) if (tand_ex is not None and not (isinstance(tand_ex, float) and pd.isna(tand_ex))) else None
                    nm_ex, nm_brk_ex, _ = berakna_nm(sel, tand_ex, row.get("Diameter_mm"), row.get("Stomme_mm"), cfg, klingor=kl_ex)
                    total_ex = dl_ex * kl_ex + nm_ex
                    st.info(
                        f"DL: **{dl_ex:.0f} kr/kl** × {kl_ex} kl = {dl_ex*kl_ex:.0f} kr  ·  "
                        f"NM: **{nm_ex:.0f} kr**  ·  Total: **{total_ex:.0f} kr**"
                    )
                    formel = scenario_formel(sel, cfg, dl_ex, nm_brk_ex, tand_ex, kl_ex,
                                             row.get("Diameter_mm"), row.get("Stomme_mm"))
                    with st.expander("Visa formel"):
                        st.code(formel, language=None)
                    st.session_state.manuell[idx] = {"sc": sel, "custom_steps": None}

                elif idx in st.session_state.manuell:
                    del st.session_state.manuell[idx]

        if st.button("Tillämpa manuella klassificeringar", type="primary"):
            st.rerun()

    # ── Export ───────────────────────────────────────────────────────────────
    st.divider()
    buf = bygg_excel(df, cfg)
    st.download_button(
        "Ladda ner Excel-rapport", data=buf,
        file_name=f"COPQ_rapport_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ══════════════════════════════════════════════════════════════════════════════
# DATAKVALITET
# ══════════════════════════════════════════════════════════════════════════════
with tab_dkv:
    st.markdown("### Datakvalitet")
    st.markdown("Mätetal för datakvaliteten i den uppladdade filen.")

    # ── Rad 1: Totalt / Klassificerade / Oklassificerade ─────────────────────
    dk1, dk2, dk3 = st.columns(3)
    with dk1:
        st.markdown(
            f'<div class="mc"><div class="ml">Totalt antal ärenden</div>'
            f'<div class="mv">{n_tot}</div></div>',
            unsafe_allow_html=True)
    with dk2:
        cls_color = "green" if klass_pct >= 80 else "yellow"
        st.markdown(
            f'<div class="mc"><div class="ml">Klassificerade ärenden</div>'
            f'<div class="mv {cls_color}">{n_kl} ({klass_pct:.1f}%)</div></div>',
            unsafe_allow_html=True)
    with dk3:
        okl_color = "yellow" if n_ok > 0 else "green"
        okl_pct   = round(n_ok / n_tot * 100, 1) if n_tot > 0 else 0
        st.markdown(
            f'<div class="mc"><div class="ml">Oklassificerade ärenden</div>'
            f'<div class="mv {okl_color}">{n_ok} ({okl_pct:.1f}%)</div></div>',
            unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Rad 2: Prefix / Manuella / Saknar tandantal ───────────────────────────
    dk4, dk5, dk6 = st.columns(3)
    with dk4:
        st.markdown(
            f'<div class="mc"><div class="ml">Klassificerade via prefix</div>'
            f'<div class="mv blue">{n_pref} ({pref_pct:.1f}%)</div></div>',
            unsafe_allow_html=True)
    with dk5:
        man_pct = round(n_man / n_kl * 100, 1) if n_kl > 0 else 0
        st.markdown(
            f'<div class="mc"><div class="ml">Manuellt klassificerade</div>'
            f'<div class="mv blue">{n_man} ({man_pct:.1f}%)</div></div>',
            unsafe_allow_html=True)
    with dk6:
        miss_color = "red" if n_miss > 0 else "green"
        st.markdown(
            f'<div class="mc"><div class="ml">Saknar tandantal (NM ej beräknad)</div>'
            f'<div class="mv {miss_color}">{n_miss}</div></div>',
            unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Klassificeringsöversikt per källa ─────────────────────────────────────
    st.markdown("### Klassificeringsöversikt per källa")
    if "Kalla" in df.columns:
        kalla_summ = (
            df.groupby(df["Kalla"].fillna("oklassificerad"))
            .agg(
                **{"Antal ärenden":      ("Ärende", "count")},
            )
            .reset_index()
        )
        kalla_summ.columns = ["Klassificeringskälla", "Antal ärenden"]
        kalla_summ["Andel (%)"] = (kalla_summ["Antal ärenden"] / n_tot * 100).round(1).astype(str) + " %"
        st.dataframe(kalla_summ, hide_index=True, use_container_width=True)
        st.caption(
            "prefix = felbeskrivningen börjar med ett scenariokod-prefix (t.ex. S2b:10)  ·  "
            "text = automatisk texttolkning  ·  manuell = manuellt tilldelad i Analys-fliken"
        )

    # ── Prefixanvändning per scenario ─────────────────────────────────────────
    st.markdown("### Prefixanvändning per scenario")
    if n_kl > 0:
        pref_by_sc = (
            df[df["Scenario"].notna()]
            .groupby("Scenario").apply(
                lambda g: pd.Series({
                    "Totalt ärenden":         len(g),
                    "Via prefix":             int((g["Kalla"] == "prefix").sum()),
                })
            )
            .reset_index()
        )
        pref_by_sc["Prefixandel (%)"] = (
            pref_by_sc["Via prefix"] / pref_by_sc["Totalt ärenden"] * 100
        ).round(1).astype(str) + " %"
        pref_by_sc["Scenariobeskrivning"] = pref_by_sc["Scenario"].map(cfg["scenario_info"])
        display_pref = pref_by_sc[[
            "Scenario", "Scenariobeskrivning", "Totalt ärenden", "Via prefix", "Prefixandel (%)",
        ]]
        st.dataframe(display_pref, hide_index=True, use_container_width=True)
        st.caption(
            "Hög prefixandel (>80%) innebär att felbeskrivningarna är välstrukturerade med explicit scenariomärkning. "
            "Låg andel innebär att New Material-kostnader (t.ex. tandantal för silver) kan saknas och behöva kompletteras manuellt."
        )
    else:
        st.info("Inga klassificerade ärenden – ingen prefixstatistik att visa.")

    # ── Ärenden som saknar tandantal ──────────────────────────────────────────
    if n_miss > 0:
        st.markdown("### Ärenden som saknar tandantal")
        miss_df = df[df["Missing_tand"] == True][
            ["Ärende", "Artikelbenämning", "Felbeskrivning", "Scenario", "Kalla", "Felantal"]
        ].copy()
        miss_df.columns = [
            "Ärende", "Artikelbenämning", "Felbeskrivning",
            "Scenario", "Klassificeringskälla", "Antal klingor",
        ]
        st.dataframe(miss_df, hide_index=True, use_container_width=True)
        st.caption(
            f"Dessa {n_miss} ärenden har ett scenario som kräver tandantal (S2a/S2b/S3) "
            "men siffran saknas i felbeskrivningen. Lägg till prefixformat t.ex. "
            "`S2b:10` (= 10 tänder totalt) för att aktivera NM-beräkningen."
        )
