#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apostas_multiplas.py
Fontes: API-Football + football-data.org
Corrigido: season, mais ligas ativas em agosto, melhor tratamento de erros
"""

import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import pandas as pd

# ============================================================
# KEYS
# ============================================================

API_FOOTBALL_KEY = (
    st.secrets.get("API_FOOTBALL_KEY")
    or os.environ.get("API_FOOTBALL_KEY")
    or "SUA_CHAVE_API_FOOTBALL"
)

API_KEY_FD = (
    st.secrets.get("API_KEY")
    or os.environ.get("FOOTBALL_DATA_API_KEY")
    or "SUA_CHAVE_FD"
)

FUSO = ZoneInfo("America/Sao_Paulo")
JOGOS_RECENTES = 5
MIN_JOGOS = 3
TAMANHOS = [3, 5, 8]
TOP_N = 12
MAX_JOGOS = 60

# ============================================================
# LIGAS (foco em jogos disponíveis em agosto)
# ============================================================

LIGAS_AF = {
    # UEFA
    2: "Champions League",
    3: "Europa League",
    848: "Conference League",
    # Grandes ligas (quando começarem)
    39: "Premier League",
    140: "La Liga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    # Brasil / América
    71: "Brasileirão Série A",
    72: "Brasileirão Série B",
    13: "Copa Libertadores",
    11: "Copa Sudamericana",
    128: "Liga Profesional (Argentina)",
    262: "Liga MX",
    253: "MLS",
    667: "Leagues Cup",          # importante em agosto
    # Outras
    88: "Eredivisie",
    94: "Liga Portugal",
    40: "Championship",
    203: "Süper Lig",
    144: "Belgian Pro League",
    179: "Scottish Premiership",
    113: "Allsvenskan",
    119: "Superliga (Dinamarca)",
    103: "Eliteserien",
    106: "Ekstraklasa",
    197: "Super League Greece",
    207: "Swiss Super League",
    218: "Austrian Bundesliga",
    307: "Saudi Pro League",
    98: "J1 League",
    292: "K League 1",
    169: "Chinese Super League",
    188: "A-League",
}

# ============================================================
# MODELS
# ============================================================

@dataclass
class Selecao:
    jogo: str
    liga: str
    mercado: str
    confianca_pct: float
    odd_estimada: float
    odd_real: Optional[float] = None
    fonte: str = "AF"

@dataclass
class Multipla:
    nome: str
    tamanho: int
    descricao: str
    selecoes: list = field(default_factory=list)

    @property
    def odd_total(self):
        o = 1.0
        for s in self.selecoes:
            o *= (s.odd_real or s.odd_estimada)
        return round(o, 2)

    @property
    def confianca_media(self):
        if not self.selecoes: return 0.0
        return round(sum(s.confianca_pct for s in self.selecoes)/len(self.selecoes), 1)

    @property
    def chance_real(self):
        if not self.selecoes: return 0.0
        p = 1.0
        for s in self.selecoes:
            p *= s.confianca_pct / 100
        return round(p * 100, 2)

# ============================================================
# HELPERS
# ============================================================

def agora():
    return datetime.now(FUSO)

def hoje():
    return agora().strftime("%Y-%m-%d")

def odd_prob(p):
    p = max(min(p, 99), 1) / 100
    return round(1 / p, 2)

# ============================================================
# API-FOOTBALL
# ============================================================

def af_get(endpoint, params=None):
    url = f"https://v3.football.api-sports.io/{endpoint}"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=15)
        if r.status_code == 429:
            time.sleep(12)
            r = requests.get(url, headers=headers, params=params or {}, timeout=15)
        if r.status_code != 200:
            return None
        d = r.json()
        return None if d.get("errors") else d
    except Exception:
        return None

def af_jogos(ligas_ids):
    """Tenta season atual e anterior para maximizar resultados."""
    year = agora().year
    seasons = [year, year - 1]
    jogos = []
    erros = []

    for lid in ligas_ids:
        if len(jogos) >= MAX_JOGOS:
            break
        found = False
        for season in seasons:
            data = af_get("fixtures", {"date": hoje(), "league": lid, "season": season})
            if not data:
                continue
            resp = data.get("response", [])
            if not resp:
                continue
            for item in resp:
                if len(jogos) >= MAX_JOGOS:
                    break
                fx = item.get("fixture", {})
                status = fx.get("status", {}).get("short", "")
                if status in ("FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD"):
                    continue
                teams = item.get("teams", {})
                league = item.get("league", {})
                home = teams.get("home", {})
                away = teams.get("away", {})
                jogos.append({
                    "id": fx.get("id"),
                    "homeID": home.get("id"),
                    "awayID": away.get("id"),
                    "home": home.get("name", "?"),
                    "away": away.get("name", "?"),
                    "liga": league.get("name", LIGAS_AF.get(lid, "Liga")),
                    "fonte": "AF"
                })
                found = True
            if found:
                break
            time.sleep(0.12)
        if not found:
            erros.append(LIGAS_AF.get(lid, str(lid)))
        time.sleep(0.12)
    return jogos, erros

def af_forma(team_id):
    if "cache_forma" not in st.session_state:
        st.session_state.cache_forma = {}
    if team_id in st.session_state.cache_forma:
        return st.session_state.cache_forma[team_id]

    data = af_get("fixtures", {"team": team_id, "last": JOGOS_RECENTES, "status": "FT"})
    if not data:
        st.session_state.cache_forma[team_id] = None
        return None
    js = data.get("response", [])
    if len(js) < MIN_JOGOS:
        st.session_state.cache_forma[team_id] = None
        return None

    vit = btts = over = 0
    for j in js:
        g = j.get("goals", {})
        gc = g.get("home") or 0
        gf = g.get("away") or 0
        hid = j.get("teams", {}).get("home", {}).get("id")
        if team_id == hid:
            if gc > gf: vit += 1
        else:
            if gf > gc: vit += 1
        if gc > 0 and gf > 0: btts += 1
        if gc + gf > 2.5: over += 1

    n = len(js)
    f = {
        "win": round(vit / n * 100, 1),
        "btts": round(btts / n * 100, 1),
        "over": round(over / n * 100, 1)
    }
    st.session_state.cache_forma[team_id] = f
    return f

def af_odds(fid):
    data = af_get("odds", {"fixture": fid})
    if not data or not data.get("response"):
        return {}
    try:
        books = data["response"][0].get("bookmakers", [])
        if not books:
            return {}
        for b in books[0].get("bets", []):
            if b.get("name") == "Match Winner":
                return {v["value"]: float(v["odd"]) for v in b.get("values", [])}
    except Exception:
        pass
    return {}

# ============================================================
# FOOTBALL-DATA.ORG
# ============================================================

def fd_get(endpoint, params=None):
    if not API_KEY_FD or API_KEY_FD in ("SUA_CHAVE_FD", ""):
        return None
    url = f"https://api.football-data.org/v4/{endpoint}"
    headers = {"X-Auth-Token": API_KEY_FD}
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=15)
        if r.status_code == 429:
            time.sleep(60)
            r = requests.get(url, headers=headers, params=params or {}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def fd_jogos():
    data = fd_get("matches", {"dateFrom": hoje(), "dateTo": hoje()})
    if not data:
        return []
    jogos = []
    for m in data.get("matches", []):
        if m.get("status") in ("FINISHED", "AWARDED"):
            continue
        home = m.get("homeTeam", {})
        away = m.get("awayTeam", {})
        comp = m.get("competition", {})
        jogos.append({
            "id": m.get("id"),
            "homeID": home.get("id"),
            "awayID": away.get("id"),
            "home": home.get("name") or home.get("shortName", "?"),
            "away": away.get("name") or away.get("shortName", "?"),
            "liga": comp.get("name", "Liga"),
            "fonte": "FD"
        })
    return jogos

# ============================================================
# ANÁLISE
# ============================================================

def analisar(j):
    res = []
    hid, aid = j.get("homeID"), j.get("awayID")
    if not hid or not aid:
        return res
    nome = f"{j['home']} x {j['away']}"
    liga = j["liga"]
    fonte = j.get("fonte", "AF")

    if fonte == "AF":
        fh = af_forma(hid)
        fa = af_forma(aid)
        odds = af_odds(j["id"]) if j.get("id") else {}
    else:
        fh = fa = {"win": 48.0, "btts": 52.0, "over": 52.0}
        odds = {}

    if fh:
        res.append(Selecao(nome, liga, f"Vitória do {j['home']}", fh["win"],
                           odd_prob(fh["win"]), odds.get("Home"), fonte))
    if fa:
        res.append(Selecao(nome, liga, f"Vitória do {j['away']}", fa["win"],
                           odd_prob(fa["win"]), odds.get("Away"), fonte))
    if fh and fa:
        btts = round((fh["btts"] + fa["btts"]) / 2, 1)
        over = round((fh["over"] + fa["over"]) / 2, 1)
        res.append(Selecao(nome, liga, "Ambos marcam (BTTS)", btts, odd_prob(btts), None, fonte))
        res.append(Selecao(nome, liga, "Mais de 2.5 gols", over, odd_prob(over), None, fonte))
    return res

def top_sel(cands):
    best = {}
    for s in cands:
        if s.jogo not in best or s.confianca_pct > best[s.jogo].confianca_pct:
            best[s.jogo] = s
    return sorted(best.values(), key=lambda x: x.confianca_pct, reverse=True)[:TOP_N]

def multiplas(cands):
    orden = sorted(cands, key=lambda s: s.confianca_pct, reverse=True)
    vistos, rank = set(), []
    for s in orden:
        if s.jogo not in vistos:
            rank.append(s)
            vistos.add(s.jogo)
    desc = {3: "Menor risco", 5: "Equilíbrio", 8: "Maior risco"}
    emoji = {3: "🟢", 5: "🟡", 8: "🔴"}
    return [Multipla(f"{emoji[t]} MÚLTIPLA DE {t}", t, desc[t], rank[:t]) for t in TAMANHOS]

# ============================================================
# UI
# ============================================================

st.set_page_config(page_title="Apostas Múltiplas", page_icon="⚽", layout="wide")
st.title("⚽ Apostas Múltiplas")
st.caption(f"{agora().strftime('%d/%m/%Y %H:%M')} (Brasília) • API-Football + football-data.org")

if API_FOOTBALL_KEY == "SUA_CHAVE_API_FOOTBALL":
    st.error("Configure API_FOOTBALL_KEY nos Secrets.")
    st.stop()

with st.expander("⚙️ Ligas (selecione as ativas)", expanded=True):
    nomes = list(dict.fromkeys(LIGAS_AF.values()))
    # Padrão: UEFA + América + algumas europeias
    default = ["Champions League", "Europa League", "Conference League",
               "Brasileirão Série A", "Liga Profesional (Argentina)", "Liga MX",
               "MLS", "Leagues Cup", "Copa Libertadores", "Copa Sudamericana"]
    default = [d for d in default if d in nomes]
    sel = st.multiselect("Ligas", nomes, default=default)
    ids = [k for k, v in LIGAS_AF.items() if v in sel]

usar_fd = st.checkbox("Incluir football-data.org", value=True)

if st.button("🔄 Gerar sugestões", type="primary"):
    with st.spinner("Buscando jogos de hoje..."):
        jogos, erros_af = af_jogos(ids) if ids else ([], [])
        if usar_fd:
            jogos += fd_jogos()

        # remove duplicados
        seen = set()
        unicos = []
        for j in jogos:
            key = f"{j['home'].lower()}|{j['away'].lower()}"
            if key not in seen:
                seen.add(key)
                unicos.append(j)
        jogos = unicos[:MAX_JOGOS]

        if not jogos:
            st.warning("Nenhum jogo encontrado para hoje nas ligas selecionadas.")
            if erros_af:
                st.caption(f"Ligas sem jogos retornados: {', '.join(erros_af[:8])}")
            st.info("Dica: Em agosto muitas ligas europeias ainda não começaram. Use Champions, Conference, Brasileirão, MLS, Leagues Cup, Libertadores.")
            st.stop()

        st.success(f"{len(jogos)} jogo(s) encontrados em {len(set(j['liga'] for j in jogos))} ligas")

        cands = []
        bar = st.progress(0)
        for i, j in enumerate(jogos):
            cands.extend(analisar(j))
            bar.progress((i + 1) / len(jogos))
            time.sleep(0.1)

        if not cands:
            st.warning("Jogos encontrados, mas sem estatísticas suficientes de forma recente.")
            st.stop()

        top = top_sel(cands)
        multis = multiplas(cands)
        st.session_state.ultima = {"top": top, "multiplas": multis, "qtd_jogos": len(jogos)}
        st.success("Sugestões geradas!")

if "ultima" in st.session_state:
    top = st.session_state.ultima["top"]
    multis = st.session_state.ultima["multiplas"]

    st.subheader(f"🏆 TOP {len(top)}")
    for i, s in enumerate(top, 1):
        o = f"Odd real {s.odd_real}" if s.odd_real else f"Odd est. {s.odd_estimada}"
        st.markdown(f"**{i}. [{s.liga}] {s.jogo}** — {s.mercado}  \n**{s.confianca_pct}%** | {o}")

    for m in multis:
        st.subheader(m.nome)
        st.caption(m.descricao)
        st.markdown(f"Odd total **{m.odd_total}** | Acerto real **{m.chance_real}%** | Média {m.confianca_media}%")
        for s in m.selecoes:
            o = s.odd_real or s.odd_estimada
            st.markdown(f"- [{s.liga}] {s.jogo}: **{s.mercado}** ({s.confianca_pct}% | odd {o})")

    rows = [{
        "multipla": m.nome, "liga": s.liga, "jogo": s.jogo,
        "mercado": s.mercado, "confianca": s.confianca_pct,
        "odd": s.odd_real or s.odd_estimada, "fonte": s.fonte
    } for m in multis for s in m.selecoes]
    if rows:
        st.download_button("📥 Baixar CSV", pd.DataFrame(rows).to_csv(index=False).encode("utf-8"),
                           f"sugestoes_{hoje()}.csv", "text/csv")

st.caption("Corrigido season + mais ligas de agosto • Cache ativo")
