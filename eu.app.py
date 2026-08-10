#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apostas_multiplas.py — versão Streamlit

Gera sugestões de apostas múltiplas com dados da API gratuita
football-data.org (api.football-data.org/v4).
"""

import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import streamlit as st

# ============================================================
# CONFIGURAÇÃO
# ============================================================

# Preferência: st.secrets["API_KEY"]  (recomendado no Streamlit Cloud)
# Fallback: variável de ambiente ou valor abaixo
API_KEY = (
    st.secrets.get("API_KEY", None)
    or os.environ.get("FOOTBALL_DATA_API_KEY")
    or "SUA_CHAVE_AQUI"
)

BASE_URL = "https://api.football-data.org/v4"
FUSO_HORARIO = ZoneInfo("America/Sao_Paulo")
INTERVALO_MINIMO_ENTRE_CHAMADAS = 6.5
JOGOS_RECENTES = 5
MINIMO_JOGOS_PARA_ANALISE = 3
TAMANHOS_MULTIPLA = [3, 5, 8]
TAMANHO_TOP = 10
MAX_POR_PAGINA = 100

PASTA_SAIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
CAMINHO_BANCO = os.path.join(PASTA_SAIDA, "historico_apostas.db")

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

def iso_para_unix(iso_str: Optional[str]) -> int:
    if not iso_str:
        return 0
    try:
        iso_str = iso_str.replace("Z", "+00:00")
        return int(datetime.fromisoformat(iso_str).timestamp())
    except ValueError:
        return 0

def unix_para_sp_str(timestamp_unix: int) -> str:
    if not timestamp_unix:
        return "horário indisponível"
    dt_utc = datetime.fromtimestamp(timestamp_unix, tz=timezone.utc)
    dt_sp = dt_utc.astimezone(FUSO_HORARIO)
    return dt_sp.strftime("%d/%m/%Y %H:%M") + " (Brasília)"

# ============================================================
# API
# ============================================================

_ULTIMA_CHAMADA = {"quando": 0.0}

def _respeitar_rate_limit():
    decorrido = time.monotonic() - _ULTIMA_CHAMADA["quando"]
    if decorrido < INTERVALO_MINIMO_ENTRE_CHAMADAS:
        time.sleep(INTERVALO_MINIMO_ENTRE_CHAMADAS - decorrido)
    _ULTIMA_CHAMADA["quando"] = time.monotonic()

def api_get(endpoint: str, params: dict = None) -> Optional[dict]:
    _respeitar_rate_limit()
    url = f"{BASE_URL}/{endpoint}"
    headers = {"X-Auth-Token": API_KEY}
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=20)
        if resp.status_code == 429:
            time.sleep(60)
            resp = requests.get(url, headers=headers, params=params or {}, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None

def api_get_paginado(endpoint: str, params: dict, chave_lista: str, max_paginas: int = 20) -> list:
    todos = []
    offset = 0
    limite = params.get("limit", MAX_POR_PAGINA)
    for _ in range(max_paginas):
        data = api_get(endpoint, {**params, "limit": limite, "offset": offset})
        if not data:
            break
        lote = data.get(chave_lista, [])
        if not lote:
            break
        todos.extend(lote)
        if len(lote) < limite:
            break
        offset += limite
    return todos

def chave_configurada() -> bool:
    return bool(API_KEY) and API_KEY != "SUA_CHAVE_AQUI"

# ============================================================
# NORMALIZAÇÃO E BUSCA
# ============================================================

def normalizar_jogo(jogo_api: dict) -> dict:
    score = jogo_api.get("score", {}) or {}
    tempo_cheio = score.get("fullTime", {}) or {}
    home = jogo_api.get("homeTeam", {}) or {}
    away = jogo_api.get("awayTeam", {}) or {}
    competicao = jogo_api.get("competition", {}) or {}
    status_raw = jogo_api.get("status")
    status = "complete" if status_raw == "FINISHED" else status_raw
    return {
        "id": jogo_api.get("id"),
        "homeID": home.get("id"),
        "awayID": away.get("id"),
        "home_name": home.get("name") or home.get("shortName") or "Mandante",
        "away_name": away.get("name") or away.get("shortName") or "Visitante",
        "homeGoalCount": tempo_cheio.get("home"),
        "awayGoalCount": tempo_cheio.get("away"),
        "status": status,
        "date_unix": iso_para_unix(jogo_api.get("utcDate")),
        "competition_name": competicao.get("name", "Liga"),
        "competition_code": competicao.get("code"),
    }

def obter_jogos_do_dia() -> list:
    hoje_sp = data_sp_str()
    data = api_get("matches", {"dateFrom": hoje_sp, "dateTo": hoje_sp})
    if not data:
        return []
    jogos_raw = data.get("matches", [])
    return [
        normalizar_jogo(j) for j in jogos_raw
        if j.get("status") not in ("FINISHED", "AWARDED")
    ]

_CACHE_JOGOS_POR_COMPETICAO = {}

def obter_todos_jogos_competicao(codigo_competicao: str) -> list:
    if not codigo_competicao:
        return []
    if codigo_competicao in _CACHE_JOGOS_POR_COMPETICAO:
        return _CACHE_JOGOS_POR_COMPETICAO[codigo_competicao]
    jogos_raw = api_get_paginado(
        f"competitions/{codigo_competicao}/matches",
        {"status": "FINISHED"},
        chave_lista="matches",
    )
    jogos = [normalizar_jogo(j) for j in jogos_raw]
    _CACHE_JOGOS_POR_COMPETICAO[codigo_competicao] = jogos
    return jogos

# ============================================================
# ANÁLISE
# ============================================================

def resultado_do_time(jogo: dict, team_id: int) -> Optional[str]:
    home_id = jogo.get("homeID")
    away_id = jogo.get("awayID")
    gols_casa = jogo.get("homeGoalCount")
    gols_fora = jogo.get("awayGoalCount")
    if gols_casa is None or gols_fora is None:
        return None
    if team_id == home_id:
        if gols_casa > gols_fora:
            return "vitoria"
        return "empate" if gols_casa == gols_fora else "derrota"
    if team_id == away_id:
        if gols_fora > gols_casa:
            return "vitoria"
        return "empate" if gols_casa == gols_fora else "derrota"
    return None

def calcular_forma_recente(team_id: int, todos_jogos: list, data_referencia_unix: int,
                           mandante: Optional[bool], n: int = JOGOS_RECENTES) -> Optional[dict]:
    jogos_do_time = [
        j for j in todos_jogos
        if j.get("status") == "complete"
        and j.get("date_unix", 0) < data_referencia_unix
        and (j.get("homeID") == team_id or j.get("awayID") == team_id)
    ]
    if mandante is True:
        jogos_do_time = [j for j in jogos_do_time if j.get("homeID") == team_id]
    elif mandante is False:
        jogos_do_time = [j for j in jogos_do_time if j.get("awayID") == team_id]
    jogos_do_time.sort(key=lambda j: j.get("date_unix", 0), reverse=True)
    recentes = jogos_do_time[:n]
    if len(recentes) < MINIMO_JOGOS_PARA_ANALISE:
        return None
    vitorias = btts = over25 = 0
    for j in recentes:
        if resultado_do_time(j, team_id) == "vitoria":
            vitorias += 1
        gols_casa = j.get("homeGoalCount", 0) or 0
        gols_fora = j.get("awayGoalCount", 0) or 0
        if gols_casa > 0 and gols_fora > 0:
            btts += 1
        if (gols_casa + gols_fora) > 2.5:
            over25 += 1
    total = len(recentes)
    return {
        "win_pct": round(vitorias / total * 100, 1),
        "btts_pct": round(btts / total * 100, 1),
        "over25_pct": round(over25 / total * 100, 1),
        "jogos_considerados": total,
    }

def probabilidade_para_odd(prob_pct: float) -> float:
    prob = max(min(prob_pct, 99), 1) / 100
    return round(1 / prob, 2)

def analisar_jogo(jogo: dict, todos_jogos_liga: list, nome_liga: str) -> list:
    candidatas = []
    home_id = jogo.get("homeID")
    away_id = jogo.get("awayID")
    if not home_id or not away_id:
        return candidatas
    home_nome = jogo.get("home_name", "Mandante")
    away_nome = jogo.get("away_name", "Visitante")
    nome_jogo = f"{home_nome} x {away_nome}"
    data_ref = jogo.get("date_unix", 9999999999)
    forma_casa = calcular_forma_recente(home_id, todos_jogos_liga, data_ref, mandante=True)
    forma_fora = calcular_forma_recente(away_id, todos_jogos_liga, data_ref, mandante=False)
    if forma_casa is not None:
        candidatas.append(Selecao(
            jogo=nome_jogo, liga=nome_liga,
            mercado=f"Vitória do {home_nome}",
            confianca_pct=forma_casa["win_pct"],
            odd_estimada=probabilidade_para_odd(forma_casa["win_pct"]),
        ))
    if forma_fora is not None:
        candidatas.append(Selecao(
            jogo=nome_jogo, liga=nome_liga,
            mercado=f"Vitória do {away_nome}",
            confianca_pct=forma_fora["win_pct"],
            odd_estimada=probabilidade_para_odd(forma_fora["win_pct"]),
        ))
    if forma_casa is not None and forma_fora is not None:
        btts_media = round((forma_casa["btts_pct"] + forma_fora["btts_pct"]) / 2, 1)
        candidatas.append(Selecao(
            jogo=nome_jogo, liga=nome_liga,
            mercado="Ambos marcam (BTTS) - Sim",
            confianca_pct=btts_media,
            odd_estimada=probabilidade_para_odd(btts_media),
        ))
        over_media = round((forma_casa["over25_pct"] + forma_fora["over25_pct"]) / 2, 1)
        candidatas.append(Selecao(
            jogo=nome_jogo, liga=nome_liga,
            mercado="Mais de 2.5 gols",
            confianca_pct=over_media,
            odd_estimada=probabilidade_para_odd(over_media),
        ))
    return candidatas

# ============================================================
# TOP E MÚLTIPLAS
# ============================================================

def montar_top(todas_candidatas: list, tamanho: int = TAMANHO_TOP) -> list:
    melhor_por_jogo = {}
    for s in todas_candidatas:
        atual = melhor_por_jogo.get(s.jogo)
        if atual is None or s.confianca_pct > atual.confianca_pct:
            melhor_por_jogo[s.jogo] = s
    ordenado = sorted(melhor_por_jogo.values(), key=lambda s: s.confianca_pct, reverse=True)
    return ordenado[:tamanho]

def montar_multiplas(todas_candidatas: list, tamanhos: list = TAMANHOS_MULTIPLA) -> list:
    ordenadas = sorted(todas_candidatas, key=lambda s: s.confianca_pct, reverse=True)
    vistos = set()
    ranking_sem_repetir = []
    for s in ordenadas:
        if s.jogo not in vistos:
            ranking_sem_repetir.append(s)
            vistos.add(s.jogo)
    multiplas = []
    descricoes = {
        3: "Múltipla enxuta com as seleções de maior confiança do dia. Menor risco.",
        5: "Múltipla intermediária — equilíbrio entre número de seleções e confiança.",
        8: "Múltipla mais arrojada — mais seleções. Maior risco e maior retorno potencial.",
    }
    emojis = {3: "🟢", 5: "🟡", 8: "🔴"}
    for tamanho in tamanhos:
        selecoes = ranking_sem_repetir[:tamanho]
        multiplas.append(Multipla(
            nome=f"{emojis.get(tamanho, '⚪')} MÚLTIPLA DE {tamanho}",
            tamanho=tamanho,
            descricao=descricoes.get(tamanho, f"Múltipla com {tamanho} seleções."),
            selecoes=selecoes,
        ))
    return multiplas

# ============================================================
# BANCO (opcional no Streamlit)
# ============================================================

def inicializar_banco():
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    con = sqlite3.connect(CAMINHO_BANCO)
    con.execute("""
        CREATE TABLE IF NOT EXISTS sugestoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_geracao TEXT NOT NULL,
            tamanho_multipla INTEGER NOT NULL,
            jogo TEXT NOT NULL,
            liga TEXT,
            mercado TEXT NOT NULL,
            confianca_pct REAL NOT NULL,
            odd_estimada REAL NOT NULL,
            resultado TEXT DEFAULT NULL
        )
    """)
    con.commit()
    con.close()

def salvar_sugestoes_no_banco(multiplas: list) -> None:
    try:
        inicializar_banco()
        con = sqlite3.connect(CAMINHO_BANCO)
        agora = agora_sp().strftime("%Y-%m-%d %H:%M:%S")
        for m in multiplas:
            for s in m.selecoes:
                con.execute(
                    """INSERT INTO sugestoes
                       (data_geracao, tamanho_multipla, jogo, liga, mercado,
                        confianca_pct, odd_estimada)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (agora, m.tamanho, s.jogo, s.liga, s.mercado,
                     s.confianca_pct, s.odd_estimada),
                )
        con.commit()
        con.close()
    except Exception:
        pass  # SQLite pode falhar em ambiente read-only do Streamlit Cloud

# ============================================================
# INTERFACE STREAMLIT
# ============================================================

st.set_page_config(page_title="Apostas Múltiplas", page_icon="⚽", layout="wide")

st.title("⚽ Sugestões de Apostas Múltiplas")
st.caption(f"Dados de {agora_sp().strftime('%d/%m/%Y %H:%M')} (Brasília) • football-data.org")

if not chave_configurada():
    st.error("API_KEY não configurada.")
    st.markdown("""
    1. Cadastre-se grátis em [football-data.org](https://www.football-data.org/client/register)  
    2. Copie o token  
    3. No Streamlit Cloud → **Settings → Secrets** adicione:  
       ```
       API_KEY = "seu_token_aqui"
       ```
    """)
    st.stop()

if st.button("🔄 Gerar sugestões de hoje", type="primary"):
    with st.spinner("Buscando jogos e calculando forma recente..."):
        jogos = obter_jogos_do_dia()
        if not jogos:
            st.warning("Nenhum jogo encontrado para hoje nas competições do plano gratuito.")
            st.stop()

        st.info(f"{len(jogos)} jogo(s) encontrado(s). Analisando...")

        todas_candidatas = []
        progress = st.progress(0)
        for i, j in enumerate(jogos):
            codigo = j.get("competition_code")
            todos_jogos_liga = obter_todos_jogos_competicao(codigo)
            nome_liga = j.get("competition_name", "Liga")
            candidatas = analisar_jogo(j, todos_jogos_liga, nome_liga)
            todas_candidatas.extend(candidatas)
            progress.progress((i + 1) / len(jogos))

        if not todas_candidatas:
            st.warning("Não foi possível calcular estatísticas suficientes.")
            st.stop()

        top = montar_top(todas_candidatas)
        multiplas = montar_multiplas(todas_candidatas)
        salvar_sugestoes_no_banco(multiplas)

        st.success("Sugestões geradas!")

        # TOP 10
        st.subheader(f"🏆 TOP {len(top)} seleções do dia")
        for i, s in enumerate(top, 1):
            st.markdown(
                f"**{i}. [{s.liga}] {s.jogo}** — {s.mercado}  \n"
                f"Confiança: **{s.confianca_pct}%** | Odd estimada: {s.odd_estimada}"
            )

        # Múltiplas
        for m in multiplas:
            st.subheader(m.nome)
            st.caption(m.descricao)
            st.markdown(
                f"Odd total estimada: **{m.odd_total_estimada}**  \n"
                f"% de acerto real da múltipla: **{m.chance_acerto_real_pct}%**  \n"
                f"Confiança média: {m.confianca_media}%"
            )
            for s in m.selecoes:
                st.markdown(
                    f"- [{s.liga}] {s.jogo}: **{s.mercado}** "
                    f"(confiança {s.confianca_pct}%, odd {s.odd_estimada})"
                )

        st.divider()
        st.caption(
            "As porcentagens refletem forma recente, não garantem resultado. "
            "Odds são estimadas. Aposte com responsabilidade."
        )
else:
    st.info("Clique no botão acima para gerar as sugestões de hoje.")
