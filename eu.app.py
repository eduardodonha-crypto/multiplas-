#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apostas_multiplas.py — Streamlit + API-Football (api-sports.io)

Fonte principal: API-Football (1.200+ ligas no plano gratuito)
"""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import streamlit as st

# ============================================================
# CONFIGURAÇÃO
# ============================================================

API_FOOTBALL_KEY = (
    st.secrets.get("API_FOOTBALL_KEY", None)
    or os.environ.get("API_FOOTBALL_KEY")
    or "SUA_CHAVE_API_FOOTBALL"
)

FUSO_HORARIO = ZoneInfo("America/Sao_Paulo")
JOGOS_RECENTES = 5
MINIMO_JOGOS_PARA_ANALISE = 3
TAMANHOS_MULTIPLA = [3, 5, 8]
TAMANHO_TOP = 10

# Máximo seguro de ligas (~25) para caber nas 100 req/dia
# (1 req por liga + 2 req por jogo analisado)
LIGAS_API_FOOTBALL = {
    39: "Premier League",
    140: "La Liga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    2: "Champions League",
    3: "Europa League",
    848: "Conference League",
    88: "Eredivisie",
    94: "Liga Portugal",
    40: "Championship",
    71: "Brasileirão Série A",
    72: "Brasileirão Série B",
    128: "Liga Profesional (Argentina)",
    262: "Liga MX",
    253: "MLS",
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
    345: "Czech First League",
    210: "Croatian HNL",
}

# ============================================================
# ESTRUTURAS
# ============================================================

@dataclass
class Selecao:
    jogo: str
    liga: str
    mercado: str
    confianca_pct: float
    odd_estimada: float

@dataclass
class Multipla:
    nome: str
    tamanho: int
    descricao: str
    selecoes: list = field(default_factory=list)

    @property
    def odd_total_estimada(self) -> float:
        odd = 1.0
        for s in self.selecoes:
            odd *= s.odd_estimada
        return round(odd, 2)

    @property
    def confianca_media(self) -> float:
        if not self.selecoes:
            return 0.0
        return round(sum(s.confianca_pct for s in self.selecoes) / len(self.selecoes), 1)

    @property
    def chance_acerto_real_pct(self) -> float:
        if not self.selecoes:
            return 0.0
        prob = 1.0
        for s in self.selecoes:
            prob *= (s.confianca_pct / 100)
        return round(prob * 100, 2)

# ============================================================
# UTILIDADES
# ============================================================

def agora_sp() -> datetime:
    return datetime.now(FUSO_HORARIO)

def data_sp_str() -> str:
    return agora_sp().strftime("%Y-%m-%d")

def probabilidade_para_odd(prob_pct: float) -> float:
    prob = max(min(prob_pct, 99), 1) / 100
    return round(1 / prob, 2)

# ============================================================
# API-FOOTBALL
# ============================================================

def api_football_get(endpoint: str, params: dict = None) -> Optional[dict]:
    url = f"https://v3.football.api-sports.io/{endpoint}"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=20)
        if resp.status_code == 429:
            time.sleep(5)
            resp = requests.get(url, headers=headers, params=params or {}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            return None
        return data
    except Exception:
        return None

def obter_jogos_do_dia() -> list:
    hoje = data_sp_str()
    season = agora_sp().year if agora_sp().month > 6 else agora_sp().year - 1
    jogos = []
    for liga_id, nome_liga in LIGAS_API_FOOTBALL.items():
        data = api_football_get("fixtures", {
            "date": hoje,
            "league": liga_id,
            "season": season
        })
        if not data:
            continue
        for item in data.get("response", []):
            fixture = item.get("fixture", {})
            teams = item.get("teams", {})
            league = item.get("league", {})
            status = fixture.get("status", {}).get("short")
            if status in ("FT", "AET", "PEN", "PST", "CANC", "ABD"):
                continue
            home = teams.get("home", {})
            away = teams.get("away", {})
            jogos.append({
                "id": fixture.get("id"),
                "homeID": home.get("id"),
                "awayID": away.get("id"),
                "home_name": home.get("name", "Mandante"),
                "away_name": away.get("name", "Visitante"),
                "date_unix": fixture.get("timestamp", 0),
                "competition_name": league.get("name", nome_liga),
                "competition_id": liga_id,
            })
        time.sleep(0.3)
    return jogos

def obter_ultimos_jogos_time(team_id: int) -> list:
    data = api_football_get("fixtures", {
        "team": team_id,
        "last": JOGOS_RECENTES,
        "status": "FT"
    })
    if not data:
        return []
    return data.get("response", [])

def calcular_forma(team_id: int) -> Optional[dict]:
    jogos = obter_ultimos_jogos_time(team_id)
    if len(jogos) < MINIMO_JOGOS_PARA_ANALISE:
        return None
    vitorias = btts = over25 = 0
    for j in jogos:
        teams = j.get("teams", {})
        goals = j.get("goals", {})
        gols_casa = goals.get("home") or 0
        gols_fora = goals.get("away") or 0
        home_id = teams.get("home", {}).get("id")
        if team_id == home_id:
            if gols_casa > gols_fora:
                vitorias += 1
        else:
            if gols_fora > gols_casa:
                vitorias += 1
        if gols_casa > 0 and gols_fora > 0:
            btts += 1
        if (gols_casa + gols_fora) > 2.5:
            over25 += 1
    total = len(jogos)
    return {
        "win_pct": round(vitorias / total * 100, 1),
        "btts_pct": round(btts / total * 100, 1),
        "over25_pct": round(over25 / total * 100, 1),
    }

def analisar_jogo(jogo: dict) -> list:
    candidatas = []
    home_id = jogo.get("homeID")
    away_id = jogo.get("awayID")
    if not home_id or not away_id:
        return candidatas
    home_nome = jogo.get("home_name")
    away_nome = jogo.get("away_name")
    nome_jogo = f"{home_nome} x {away_nome}"
    nome_liga = jogo.get("competition_name", "Liga")

    forma_casa = calcular_forma(home_id)
    forma_fora = calcular_forma(away_id)

    if forma_casa:
        candidatas.append(Selecao(
            jogo=nome_jogo, liga=nome_liga,
            mercado=f"Vitória do {home_nome}",
            confianca_pct=forma_casa["win_pct"],
            odd_estimada=probabilidade_para_odd(forma_casa["win_pct"]),
        ))
    if forma_fora:
        candidatas.append(Selecao(
            jogo=nome_jogo, liga=nome_liga,
            mercado=f"Vitória do {away_nome}",
            confianca_pct=forma_fora["win_pct"],
            odd_estimada=probabilidade_para_odd(forma_fora["win_pct"]),
        ))
    if forma_casa and forma_fora:
        btts = round((forma_casa["btts_pct"] + forma_fora["btts_pct"]) / 2, 1)
        candidatas.append(Selecao(
            jogo=nome_jogo, liga=nome_liga,
            mercado="Ambos marcam (BTTS) - Sim",
            confianca_pct=btts,
            odd_estimada=probabilidade_para_odd(btts),
        ))
        over = round((forma_casa["over25_pct"] + forma_fora["over25_pct"]) / 2, 1)
        candidatas.append(Selecao(
            jogo=nome_jogo, liga=nome_liga,
            mercado="Mais de 2.5 gols",
            confianca_pct=over,
            odd_estimada=probabilidade_para_odd(over),
        ))
    return candidatas

# ============================================================
# TOP E MÚLTIPLAS
# ============================================================

def montar_top(todas: list) -> list:
    melhor = {}
    for s in todas:
        if s.jogo not in melhor or s.confianca_pct > melhor[s.jogo].confianca_pct:
            melhor[s.jogo] = s
    return sorted(melhor.values(), key=lambda x: x.confianca_pct, reverse=True)[:TAMANHO_TOP]

def montar_multiplas(todas: list) -> list:
    ordenadas = sorted(todas, key=lambda s: s.confianca_pct, reverse=True)
    vistos = set()
    ranking = []
    for s in ordenadas:
        if s.jogo not in vistos:
            ranking.append(s)
            vistos.add(s.jogo)
    multiplas = []
    desc = {3: "Menor risco", 5: "Equilíbrio", 8: "Maior risco/retorno"}
    emoji = {3: "🟢", 5: "🟡", 8: "🔴"}
    for t in TAMANHOS_MULTIPLA:
        multiplas.append(Multipla(
            nome=f"{emoji.get(t)} MÚLTIPLA DE {t}",
            tamanho=t,
            descricao=desc.get(t, ""),
            selecoes=ranking[:t]
        ))
    return multiplas

# ============================================================
# INTERFACE
# ============================================================

st.set_page_config(page_title="Apostas Múltiplas", page_icon="⚽", layout="wide")
st.title("⚽ Sugestões de Apostas Múltiplas")
st.caption(f"{agora_sp().strftime('%d/%m/%Y %H:%M')} (Brasília) • API-Football")

if API_FOOTBALL_KEY == "SUA_CHAVE_API_FOOTBALL":
    st.error("Configure a API_FOOTBALL_KEY nos Secrets do Streamlit.")
    st.markdown("""
    1. Crie conta em [api-football.com](https://www.api-football.com/)  
    2. Copie a chave  
    3. Streamlit → **Settings → Secrets**:
    ```
    API_FOOTBALL_KEY = "sua_chave"
    ```
    """)
    st.stop()

if st.button("🔄 Gerar sugestões de hoje", type="primary"):
    with st.spinner("Buscando jogos em várias ligas..."):
        jogos = obter_jogos_do_dia()
        if not jogos:
            st.warning("Nenhum jogo encontrado para hoje.")
            st.stop()

        st.info(f"{len(jogos)} jogo(s) encontrado(s). Analisando forma...")

        todas = []
        bar = st.progress(0)
        for i, j in enumerate(jogos):
            todas.extend(analisar_jogo(j))
            bar.progress((i + 1) / len(jogos))
            time.sleep(0.3)

        if not todas:
            st.warning("Estatísticas insuficientes.")
            st.stop()

        top = montar_top(todas)
        multiplas = montar_multiplas(todas)

        st.success("Pronto!")

        st.subheader(f"🏆 TOP {len(top)}")
        for i, s in enumerate(top, 1):
            st.markdown(f"**{i}. [{s.liga}] {s.jogo}** — {s.mercado}  \nConfiança **{s.confianca_pct}%** | Odd {s.odd_estimada}")

        for m in multiplas:
            st.subheader(m.nome)
            st.caption(m.descricao)
            st.markdown(f"Odd total: **{m.odd_total_estimada}** | Acerto real: **{m.chance_acerto_real_pct}%**")
            for s in m.selecoes:
                st.markdown(f"- [{s.liga}] {s.jogo}: **{s.mercado}** ({s.confianca_pct}%)")

        st.caption("Fonte: API-Football (100 req/dia no plano gratuito). Aposte com responsabilidade.")
else:
    st.info("Clique no botão para gerar as sugestões.")
    with st.expander("Ligas incluídas"):
        st.write(", ".join(LIGAS_API_FOOTBALL.values()))
