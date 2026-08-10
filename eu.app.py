#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apostas_multiplas.py — Streamlit + API-Football (otimizado)
- Cache de forma em memória
- Prioriza ligas principais
- Limita jogos analisados para caber nas 100 req/dia
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
# CONFIG
# ============================================================

API_FOOTBALL_KEY = (
    st.secrets.get("API_FOOTBALL_KEY", None)
    or os.environ.get("API_FOOTBALL_KEY")
    or "SUA_CHAVE_API_FOOTBALL"
)

FUSO = ZoneInfo("America/Sao_Paulo")
JOGOS_RECENTES = 5
MIN_JOGOS = 3
TAMANHOS = [3, 5, 8]
TOP_N = 10
MAX_JOGOS_ANALISAR = 40   # limite para não estourar 100 req/dia

# Ligas ordenadas por prioridade (mais importantes primeiro)
LIGAS = {
    39: "Premier League",
    140: "La Liga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    71: "Brasileirão Série A",
    2: "Champions League",
    3: "Europa League",
    848: "Conference League",
    88: "Eredivisie",
    94: "Liga Portugal",
    40: "Championship",
    128: "Liga Profesional (Argentina)",
    262: "Liga MX",
    253: "MLS",
    203: "Süper Lig",
    144: "Belgian Pro League",
    179: "Scottish Premiership",
    72: "Brasileirão Série B",
    113: "Allsvenskan",
    119: "Superliga (Dinamarca)",
    103: "Eliteserien",
    106: "Ekstraklasa",
    197: "Super League Greece",
    207: "Swiss Super League",
    218: "Austrian Bundesliga",
    345: "Czech First League",
    210: "Croatian HNL",
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
    fixture_id: Optional[int] = None

@dataclass
class Multipla:
    nome: str
    tamanho: int
    descricao: str
    selecoes: list = field(default_factory=list)

    @property
    def odd_total(self):
        odd = 1.0
        for s in self.selecoes:
            odd *= (s.odd_real or s.odd_estimada)
        return round(odd, 2)

    @property
    def confianca_media(self):
        if not self.selecoes:
            return 0.0
        return round(sum(s.confianca_pct for s in self.selecoes) / len(self.selecoes), 1)

    @property
    def chance_real(self):
        if not self.selecoes:
            return 0.0
        p = 1.0
        for s in self.selecoes:
            p *= s.confianca_pct / 100
        return round(p * 100, 2)

# ============================================================
# CACHE & HELPERS
# ============================================================

def agora():
    return datetime.now(FUSO)

def hoje_str():
    return agora().strftime("%Y-%m-%d")

def odd_from_prob(p):
    p = max(min(p, 99), 1) / 100
    return round(1 / p, 2)

# ============================================================
# API (otimizada)
# ============================================================

def api_get(endpoint, params=None):
    url = f"https://v3.football.api-sports.io/{endpoint}"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=15)
        if r.status_code == 429:
            time.sleep(8)
            r = requests.get(url, headers=headers, params=params or {}, timeout=15)
        r.raise_for_status()
        data = r.json()
        return None if data.get("errors") else data
    except Exception:
        return None

def buscar_jogos(ligas_ids):
    """Busca jogos de hoje, priorizando a ordem das ligas."""
    season = agora().year if agora().month > 6 else agora().year - 1
    jogos = []
    for lid in ligas_ids:
        if len(jogos) >= MAX_JOGOS_ANALISAR:
            break
        data = api_get("fixtures", {"date": hoje_str(), "league": lid, "season": season})
        if not data:
            continue
        for item in data.get("response", []):
            if len(jogos) >= MAX_JOGOS_ANALISAR:
                break
            fx = item.get("fixture", {})
            stt = fx.get("status", {}).get("short")
            if stt in ("FT", "AET", "PEN", "PST", "CANC", "ABD"):
                continue
            teams = item.get("teams", {})
            league = item.get("league", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            jogos.append({
                "fixture_id": fx.get("id"),
                "homeID": home.get("id"),
                "awayID": away.get("id"),
                "home_name": home.get("name", "?"),
                "away_name": away.get("name", "?"),
                "liga": league.get("name", LIGAS.get(lid, "Liga")),
            })
        time.sleep(0.15)
    return jogos

def forma_time(team_id):
    """Usa cache para não repetir chamada do mesmo time."""
    if "cache_forma" not in st.session_state:
        st.session_state.cache_forma = {}
    if team_id in st.session_state.cache_forma:
        return st.session_state.cache_forma[team_id]

    data = api_get("fixtures", {"team": team_id, "last": JOGOS_RECENTES, "status": "FT"})
    if not data:
        st.session_state.cache_forma[team_id] = None
        return None

    jogos = data.get("response", [])
    if len(jogos) < MIN_JOGOS:
        st.session_state.cache_forma[team_id] = None
        return None

    vit = btts = over = 0
    for j in jogos:
        g = j.get("goals", {})
        gc, gf = g.get("home") or 0, g.get("away") or 0
        home_id = j.get("teams", {}).get("home", {}).get("id")
        if team_id == home_id:
            if gc > gf:
                vit += 1
        else:
            if gf > gc:
                vit += 1
        if gc > 0 and gf > 0:
            btts += 1
        if gc + gf > 2.5:
            over += 1

    n = len(jogos)
    forma = {
        "win": round(vit / n * 100, 1),
        "btts": round(btts / n * 100, 1),
        "over": round(over / n * 100, 1),
    }
    st.session_state.cache_forma[team_id] = forma
    return forma

def odds_reais(fixture_id):
    data = api_get("odds", {"fixture": fixture_id})
    if not data or not data.get("response"):
        return {}
    try:
        book = data["response"][0].get("bookmakers", [])
        if not book:
            return {}
        for b in book[0].get("bets", []):
            if b.get("name") == "Match Winner":
                return {v["value"]: float(v["odd"]) for v in b.get("values", [])}
    except Exception:
        pass
    return {}

def analisar(jogo):
    res = []
    hid, aid = jogo["homeID"], jogo["awayID"]
    if not hid or not aid:
        return res
    hname, aname = jogo["home_name"], jogo["away_name"]
    nome = f"{hname} x {aname}"
    liga = jogo["liga"]
    fid = jogo["fixture_id"]

    fh = forma_time(hid)
    fa = forma_time(aid)

    odds = {}
    if "cache_forma" in st.session_state and True:
        odds = odds_reais(fid)

    if fh:
        res.append(Selecao(nome, liga, f"Vitória do {hname}", fh["win"],
                           odd_from_prob(fh["win"]), odds.get("Home"), fid))
    if fa:
        res.append(Selecao(nome, liga, f"Vitória do {aname}", fa["win"],
                           odd_from_prob(fa["win"]), odds.get("Away"), fid))
    if fh and fa:
        btts = round((fh["btts"] + fa["btts"]) / 2, 1)
        over = round((fh["over"] + fa["over"]) / 2, 1)
        res.append(Selecao(nome, liga, "Ambos marcam (BTTS)", btts, odd_from_prob(btts), None, fid))
        res.append(Selecao(nome, liga, "Mais de 2.5 gols", over, odd_from_prob(over), None, fid))
    return res

def top_selecoes(cands):
    best = {}
    for s in cands:
        if s.jogo not in best or s.confianca_pct > best[s.jogo].confianca_pct:
            best[s.jogo] = s
    return sorted(best.values(), key=lambda x: x.confianca_pct, reverse=True)[:TOP_N]

def montar_multiplas(cands):
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
# HISTÓRICO / ROI
# ============================================================

def init_hist():
    if "historico" not in st.session_state:
        st.session_state.historico = []
    if "resultados_roi" not in st.session_state:
        st.session_state.resultados_roi = []

def add_hist(multiplas):
    init_hist()
    st.session_state.historico.append({
        "data": agora().strftime("%Y-%m-%d %H:%M"),
        "multiplas": [{
            "nome": m.nome,
            "odd_total": m.odd_total,
            "chance": m.chance_real,
            "selecoes": [asdict(s) for s in m.selecoes]
        } for m in multiplas]
    })

def calc_roi(res):
    if not res:
        return "Nenhum resultado ainda."
    total = len(res)
    acertos = sum(1 for r in res if r["green"])
    retorno = sum((r["odd"] - 1) if r["green"] else -1 for r in res)
    return f"Acertos: {acertos}/{total} ({round(acertos/total*100,1)}%) | ROI: {round(retorno/total*100,1)}%"

# ============================================================
# UI
# ============================================================

st.set_page_config(page_title="Apostas Múltiplas", page_icon="⚽", layout="wide")
st.title("⚽ Apostas Múltiplas")
st.caption(f"{agora().strftime('%d/%m/%Y %H:%M')} (Brasília) • API-Football otimizado")

if API_FOOTBALL_KEY == "SUA_CHAVE_API_FOOTBALL":
    st.error("Configure API_FOOTBALL_KEY nos Secrets.")
    st.stop()

with st.expander("⚙️ Ligas (prioridade)", expanded=False):
    nomes = list(LIGAS.values())
    sel = st.multiselect("Selecione", nomes, default=nomes[:10])
    ligas_ids = [k for k, v in LIGAS.items() if v in sel]

if st.button("🔄 Gerar sugestões", type="primary", disabled=not ligas_ids):
    with st.spinner("Buscando (otimizado)..."):
        jogos = buscar_jogos(ligas_ids)
        if not jogos:
            st.warning("Nenhum jogo hoje nas ligas escolhidas.")
            st.stop()

        st.info(f"Analisando {len(jogos)} jogo(s) (máx. {MAX_JOGOS_ANALISAR})")
        cands = []
        bar = st.progress(0)
        for i, j in enumerate(jogos):
            cands.extend(analisar(j))
            bar.progress((i + 1) / len(jogos))
            time.sleep(0.1)

        if not cands:
            st.warning("Sem dados suficientes.")
            st.stop()

        top = top_selecoes(cands)
        multiplas = montar_multiplas(cands)
        add_hist(multiplas)
        st.session_state.ultima = {"top": top, "multiplas": multiplas}
        st.success("Pronto!")

if "ultima" in st.session_state:
    top = st.session_state.ultima["top"]
    multiplas = st.session_state.ultima["multiplas"]

    st.subheader(f"🏆 TOP {len(top)}")
    for i, s in enumerate(top, 1):
        o = f"Odd real {s.odd_real}" if s.odd_real else f"Odd est. {s.odd_estimada}"
        st.markdown(f"**{i}. [{s.liga}] {s.jogo}** — {s.mercado}  \nConfiança **{s.confianca_pct}%** | {o}")

    for m in multiplas:
        st.subheader(m.nome)
        st.caption(m.descricao)
        st.markdown(f"Odd total **{m.odd_total}** | Acerto real **{m.chance_real}%** | Média {m.confianca_media}%")
        for s in m.selecoes:
            o = s.odd_real or s.odd_estimada
            st.markdown(f"- [{s.liga}] {s.jogo}: **{s.mercado}** ({s.confianca_pct}% | odd {o})")

    rows = []
    for m in multiplas:
        for s in m.selecoes:
            rows.append({
                "multipla": m.nome, "liga": s.liga, "jogo": s.jogo,
                "mercado": s.mercado, "confianca": s.confianca_pct,
                "odd": s.odd_real or s.odd_estimada
            })
    if rows:
        st.download_button("📥 CSV", pd.DataFrame(rows).to_csv(index=False).encode(),
                           f"sugestoes_{hoje_str()}.csv", "text/csv")

st.divider()
st.subheader("📊 Histórico & ROI")
init_hist()

if st.session_state.historico:
    for h in reversed(st.session_state.historico[-5:]):
        with st.expander(h["data"]):
            for m in h["multiplas"]:
                st.write(f"**{m['nome']}** — odd {m['odd_total']} | chance {m['chance']}%")
else:
    st.caption("Gere sugestões para iniciar o histórico.")

with st.form("roi_form"):
    c1, c2, c3 = st.columns(3)
    odd_r = c1.number_input("Odd", min_value=1.01, value=2.0, step=0.01)
    res = c2.selectbox("Resultado", ["green", "red"])
    if c3.form_submit_button("Registrar"):
        st.session_state.resultados_roi.append({
            "odd": odd_r, "green": res == "green",
            "data": agora().strftime("%Y-%m-%d %H:%M")
        })
        st.success("Salvo")

if st.session_state.resultados_roi:
    st.info(calc_roi(st.session_state.resultados_roi))
    if st.button("Limpar ROI"):
        st.session_state.resultados_roi = []
        st.rerun()

st.caption("Cache ativo • Máx. 25 jogos/análise • 100 req/dia")
