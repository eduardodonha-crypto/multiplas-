#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apostas_multiplas.py

Gera diariamente sugestões de apostas múltiplas com base em dados da API
GRATUITA da football-data.org (api.football-data.org/v4).

POR QUE football-data.org?
----------------------------
A FootyStats exige assinatura paga (ou chave de teste travada em dados de
2018/2019) para qualquer sugestão real. A football-data.org tem um plano
gratuito de verdade: basta se cadastrar (sem cartão de crédito) para
receber uma chave de API válida.

O QUE MUDA EM RELAÇÃO AO PLANO PAGO DA FOOTYSTATS
-----------------------------------------------------
- Cobertura: 12 competições (Premier League, La Liga, Bundesliga, Serie A,
  Ligue 1, Champions League, Brasileirão Série A, Eredivisie, Liga
  Portugal, Championship, Copa do Mundo, Eurocopa) — não é "todas as
  ligas do mundo", mas cobre os principais campeonatos.
- Sem PPG/xG/odds pré-jogo pré-calculados: o plano gratuito não oferece
  isso. A confiança de cada seleção vem só da FORMA RECENTE calculada
  localmente (últimos N jogos de cada time).
- Rate limit de 10 requisições/minuto: o programa já respeita esse limite
  automaticamente, espaçando as chamadas.

COMO OBTER SUA CHAVE (GRATUITA)
-----------------------------------
1. Acesse https://www.football-data.org/client/register
2. Cadastre-se (grátis, sem cartão)
3. Copie o token que aparece no seu painel
4. Cole abaixo em API_KEY

FUNCIONALIDADES
----------------
- Busca automática dos jogos de hoje (fuso horário de São Paulo)
- Paginação automática nas chamadas à API
- Confiança calculada pela FORMA RECENTE de cada time (últimos N jogos)
- TOP 10 melhores seleções do dia (maior confiança, 1 por jogo)
- 3 múltiplas de tamanho fixo: 3, 5 e 8 seleções (risco crescente)
- Banco de dados histórico (SQLite) com todas as sugestões geradas
- Comando para registrar o resultado real de uma sugestão (base para ROI)
- Relatório de desempenho histórico (taxa de acerto e ROI acumulado)

AVISO IMPORTANTE
-----------------
Este programa usa estatísticas históricas (forma recente, % de vitórias,
BTTS, over/under) como indicadores de força dos times. Isso NÃO é
garantia de resultado. Aposte com responsabilidade e nunca aposte mais
do que pode perder.
"""

import argparse
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests

# ============================================================
# CONFIGURAÇÃO
# ============================================================

API_KEY = "SUA_CHAVE_AQUI"  # <-- cole aqui sua chave gratuita da football-data.org
BASE_URL = "https://api.football-data.org/v4"

FUSO_HORARIO = ZoneInfo("America/Sao_Paulo")

# Rate limit do plano gratuito: 10 requisições/minuto.
# Espaçamos as chamadas em 6.5s para nunca estourar (60s / 10 = 6s + margem).
INTERVALO_MINIMO_ENTRE_CHAMADAS = 6.5

# Competições cobertas pelo plano gratuito (informativo — a API já filtra
# automaticamente pelas competições do seu plano nas chamadas /matches)
COMPETICOES_GRATUITAS = {
    "PL": "Premier League", "PD": "La Liga", "BL1": "Bundesliga",
    "SA": "Serie A", "FL1": "Ligue 1", "CL": "Champions League",
    "DED": "Eredivisie", "PPL": "Liga Portugal", "ELC": "Championship",
    "BSA": "Brasileirão Série A", "WC": "Copa do Mundo", "EC": "Eurocopa",
}

# Quantos últimos jogos considerar para calcular "forma" de um time.
# 5 = mais reativo à fase atual do time (recomendado); 10 = mais estável,
# porém mais lento pra refletir mudanças recentes (técnico, lesões, etc).
JOGOS_RECENTES = 5

# Mínimo de jogos recentes necessários para confiar na % calculada
MINIMO_JOGOS_PARA_ANALISE = 3

# Tamanhos fixos das múltiplas geradas (risco crescente)
TAMANHOS_MULTIPLA = [3, 5, 8]

# Quantas seleções mostrar no TOP do dia
TAMANHO_TOP = 10

# Máximo de itens por página nas chamadas paginadas à API
MAX_POR_PAGINA = 100

# Onde salvar o relatório diário e o banco histórico
PASTA_SAIDA = "/mnt/user-data/outputs"
CAMINHO_BANCO = os.path.join(PASTA_SAIDA, "historico_apostas.db")


# ============================================================
# ESTRUTURAS DE DADOS
# ============================================================

@dataclass
class Selecao:
    """Uma seleção individual dentro de uma múltipla (ex: 'Time A vence')."""
    jogo: str
    liga: str
    mercado: str
    confianca_pct: float
    odd_estimada: float          # odd "justa" estimada a partir da probabilidade


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
        """Produto das probabilidades individuais: chance real da múltipla
        inteira acertar (todas as seleções precisam bater)."""
        if not self.selecoes:
            return 0.0
        prob = 1.0
        for s in self.selecoes:
            prob *= (s.confianca_pct / 100)
        return round(prob * 100, 2)


# ============================================================
# UTILIDADES DE FUSO HORÁRIO (América/São_Paulo) E DATAS DA API
# ============================================================

def agora_sp() -> datetime:
    return datetime.now(FUSO_HORARIO)


def data_sp_str() -> str:
    """Data de hoje no fuso de São Paulo, formato YYYY-MM-DD."""
    return agora_sp().strftime("%Y-%m-%d")


def iso_para_unix(iso_str: Optional[str]) -> int:
    """Converte a data ISO 8601 (utcDate) da football-data.org para unix timestamp."""
    if not iso_str:
        return 0
    try:
        iso_str = iso_str.replace("Z", "+00:00")
        return int(datetime.fromisoformat(iso_str).timestamp())
    except ValueError:
        return 0


def unix_para_sp_str(timestamp_unix: int) -> str:
    """Converte um timestamp unix (UTC) para horário de São Paulo legível."""
    if not timestamp_unix:
        return "horário indisponível"
    dt_utc = datetime.fromtimestamp(timestamp_unix, tz=timezone.utc)
    dt_sp = dt_utc.astimezone(FUSO_HORARIO)
    return dt_sp.strftime("%d/%m/%Y %H:%M") + " (Brasília)"


# ============================================================
# CAMADA DE ACESSO À API (football-data.org v4)
# ============================================================

_ULTIMA_CHAMADA = {"quando": 0.0}


def _respeitar_rate_limit():
    """Garante pelo menos INTERVALO_MINIMO_ENTRE_CHAMADAS segundos entre
    chamadas, para nunca estourar o limite de 10 req/min do plano gratuito."""
    decorrido = time.monotonic() - _ULTIMA_CHAMADA["quando"]
    if decorrido < INTERVALO_MINIMO_ENTRE_CHAMADAS:
        time.sleep(INTERVALO_MINIMO_ENTRE_CHAMADAS - decorrido)
    _ULTIMA_CHAMADA["quando"] = time.monotonic()


def api_get(endpoint: str, params: dict = None) -> Optional[dict]:
    """Faz uma chamada GET à API da football-data.org com tratamento de erro
    e respeito ao rate limit do plano gratuito."""
    _respeitar_rate_limit()
    url = f"{BASE_URL}/{endpoint}"
    headers = {"X-Auth-Token": API_KEY}
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=20)
        if resp.status_code == 429:
            print("[AVISO] Rate limit atingido, aguardando 60s...")
            time.sleep(60)
            resp = requests.get(url, headers=headers, params=params or {}, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"[ERRO] Falha ao chamar {endpoint}: {e}")
        return None
    except ValueError:
        print(f"[ERRO] Resposta inválida (não-JSON) de {endpoint}")
        return None


def api_get_paginado(endpoint: str, params: dict, chave_lista: str,
                      max_paginas: int = 20) -> list:
    """Busca todas as páginas de um endpoint que devolve listas grandes."""
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
# NORMALIZAÇÃO DOS DADOS DA API
# ============================================================

def normalizar_jogo(jogo_api: dict) -> dict:
    """
    Converte o formato de jogo da football-data.org para um formato
    interno simples, usado pelo resto do programa.
    """
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


# ============================================================
# BUSCA DE JOGOS
# ============================================================

def obter_jogos_do_dia() -> list:
    """
    Retorna os jogos de hoje (fuso de São Paulo) de todas as competições
    do plano gratuito, já normalizados.
    """
    hoje_sp = data_sp_str()
    data = api_get("matches", {"dateFrom": hoje_sp, "dateTo": hoje_sp})
    if not data:
        return []
    jogos_raw = data.get("matches", [])
    return [normalizar_jogo(j) for j in jogos_raw]


_CACHE_JOGOS_POR_COMPETICAO = {}


def obter_todos_jogos_competicao(codigo_competicao: str) -> list:
    """
    Retorna todos os jogos já finalizados da temporada atual de uma
    competição (usado para calcular a forma recente dos times). Cacheado
    em memória durante a execução para não repetir chamadas.
    """
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
# ANÁLISE ESTATÍSTICA — FORMA RECENTE
# ============================================================

def resultado_do_time(jogo: dict, team_id: int) -> Optional[str]:
    """Retorna 'vitoria', 'empate' ou 'derrota' do ponto de vista do team_id."""
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
    """
    Calcula a forma de um time nos últimos N jogos ANTES da data de
    referência (evita "vazar" resultados futuros para dentro da análise).

    mandante:
      True  -> considera só os últimos jogos em casa
      False -> considera só os últimos jogos fora
      None  -> considera os últimos jogos independente do mando de campo
    """
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

    vitorias = 0
    btts = 0
    over25 = 0
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
    """Converte uma probabilidade (%) em odd 'justa' (1/probabilidade)."""
    prob = max(min(prob_pct, 99), 1) / 100
    return round(1 / prob, 2)


def analisar_jogo(jogo: dict, todos_jogos_liga: list, nome_liga: str) -> list:
    """
    Calcula a forma recente de cada time e gera candidatas a seleção
    (vitória mandante, vitória visitante, BTTS, over 2.5) com a confiança
    baseada nesses jogos recentes.
    """
    candidatas = []

    home_id = jogo.get("homeID")
    away_id = jogo.get("awayID")
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
# TOP 10 E MÚLTIPLAS DE TAMANHO FIXO
# ============================================================

def montar_top(todas_candidatas: list, tamanho: int = TAMANHO_TOP) -> list:
    """Melhor seleção de cada jogo, ordenadas por confiança — TOP do dia."""
    melhor_por_jogo = {}
    for s in todas_candidatas:
        atual = melhor_por_jogo.get(s.jogo)
        if atual is None or s.confianca_pct > atual.confianca_pct:
            melhor_por_jogo[s.jogo] = s
    ordenado = sorted(melhor_por_jogo.values(), key=lambda s: s.confianca_pct, reverse=True)
    return ordenado[:tamanho]


def montar_multiplas(todas_candidatas: list, tamanhos: list = TAMANHOS_MULTIPLA) -> list:
    """
    Monta múltiplas de tamanho FIXO (ex: 3, 5, 8), sempre pegando as
    seleções de maior confiança disponíveis, sem repetir jogo dentro da
    mesma múltipla.
    """
    ordenadas = sorted(todas_candidatas, key=lambda s: s.confianca_pct, reverse=True)

    vistos = set()
    ranking_sem_repetir = []
    for s in ordenadas:
        if s.jogo not in vistos:
            ranking_sem_repetir.append(s)
            vistos.add(s.jogo)

    multiplas = []
    descricoes = {
        3: "Múltipla enxuta com as seleções de maior confiança do dia. Menor risco entre as três.",
        5: "Múltipla intermediária — equilíbrio entre número de seleções e confiança.",
        8: "Múltipla mais arrojada — mais seleções, incluindo algumas de confiança menor. Maior risco e maior retorno potencial.",
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
# BANCO HISTÓRICO (SQLite) — base para acompanhamento e ROI
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


def atualizar_resultado(sugestao_id: int, resultado: str) -> bool:
    """
    Registra o resultado real de uma sugestão específica.
    resultado deve ser 'green' (acertou) ou 'red' (errou).
    Uso: python3 apostas_multiplas.py --resultado 42 green
    """
    if resultado not in ("green", "red"):
        print("Resultado inválido. Use 'green' ou 'red'.")
        return False
    inicializar_banco()
    con = sqlite3.connect(CAMINHO_BANCO)
    cur = con.execute("UPDATE sugestoes SET resultado = ? WHERE id = ?", (resultado, sugestao_id))
    con.commit()
    afetado = cur.rowcount > 0
    con.close()
    return afetado


def calcular_roi_historico() -> str:
    """
    Resumo de desempenho histórico com base nas sugestões que já tiveram
    resultado registrado (via --resultado). ROI simples assumindo stake
    fixo de 1 unidade por seleção.
    """
    inicializar_banco()
    con = sqlite3.connect(CAMINHO_BANCO)
    linhas = con.execute(
        "SELECT tamanho_multipla, resultado, odd_estimada FROM sugestoes WHERE resultado IS NOT NULL"
    ).fetchall()
    con.close()

    if not linhas:
        return ("Nenhum resultado registrado ainda. Use "
                "'python3 apostas_multiplas.py --resultado <id> green|red' "
                "para começar a alimentar o histórico e calcular ROI.")

    por_tamanho = {}
    for tamanho, resultado, odd in linhas:
        stats = por_tamanho.setdefault(tamanho, {"total": 0, "acertos": 0, "retorno": 0.0})
        stats["total"] += 1
        if resultado == "green":
            stats["acertos"] += 1
            stats["retorno"] += (odd - 1)
        else:
            stats["retorno"] -= 1

    saida = ["DESEMPENHO HISTÓRICO (stake fixo de 1 unidade por seleção)", "-" * 60]
    for tamanho, s in sorted(por_tamanho.items()):
        taxa_acerto = round(s["acertos"] / s["total"] * 100, 1)
        roi = round(s["retorno"] / s["total"] * 100, 1)
        saida.append(f"Múltipla de {tamanho}: {s['acertos']}/{s['total']} acertos "
                      f"({taxa_acerto}%) | ROI: {roi}%")
    return "\n".join(saida)


# ============================================================
# RELATÓRIO
# ============================================================

def gerar_relatorio(top: list, multiplas: list) -> str:
    hoje_str = agora_sp().strftime("%d/%m/%Y %H:%M") + " (Brasília)"
    linhas = []
    linhas.append(f"SUGESTÕES DE APOSTAS MÚLTIPLAS - {hoje_str}")
    linhas.append("=" * 60)
    linhas.append("Fonte de dados: football-data.org (plano gratuito, 12 competições)")
    linhas.append("")

    linhas.append(f"🏆 TOP {len(top)} SELEÇÕES DO DIA (maior confiança, 1 por jogo)")
    linhas.append("-" * 60)
    if not top:
        linhas.append("  Nenhuma seleção disponível.")
    for i, s in enumerate(top, 1):
        linhas.append(f"  {i:>2}. [{s.liga}] {s.jogo}: {s.mercado} "
                       f"— confiança {s.confianca_pct}% (odd estimada {s.odd_estimada})")
    linhas.append("")

    for m in multiplas:
        linhas.append(f"{m.nome}  |  odd total estimada: {m.odd_total_estimada}")
        linhas.append(f"  {m.descricao}")
        linhas.append(f"  >>> % DE ACERTO REAL DA MÚLTIPLA (todas as seleções juntas): {m.chance_acerto_real_pct}%")
        linhas.append(f"      (confiança média por seleção, individualmente: {m.confianca_media}%)")
        if not m.selecoes:
            linhas.append("  Seleções insuficientes para montar essa múltipla hoje.")
        for s in m.selecoes:
            linhas.append(f"   - [{s.liga}] {s.jogo}: {s.mercado}  "
                           f"(confiança {s.confianca_pct}%, odd estimada {s.odd_estimada})")
        linhas.append("")

    linhas.append("-" * 60)
    linhas.append("COMO LER O '% DE ACERTO REAL': numa múltipla, TODAS as seleções")
    linhas.append("precisam acertar. Por isso essa % é o PRODUTO das probabilidades")
    linhas.append("individuais, não a média.")
    linhas.append("")
    linhas.append(calcular_roi_historico())
    linhas.append("")
    linhas.append("AVISO: as porcentagens acima refletem desempenho histórico (forma")
    linhas.append("recente), não garantem resultado futuro. As odds mostradas são")
    linhas.append("ESTIMADAS a partir da probabilidade, não são odds reais de casas")
    linhas.append("de apostas. Aposte com responsabilidade.")

    return "\n".join(linhas)


def salvar_relatorio(texto: str) -> str:
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    nome_arquivo = f"apostas_{agora_sp().strftime('%Y-%m-%d')}.txt"
    caminho = os.path.join(PASTA_SAIDA, nome_arquivo)
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(texto)
    return caminho


# ============================================================
# ORQUESTRAÇÃO
# ============================================================

def gerar_sugestoes_do_dia():
    if not chave_configurada():
        print("=" * 60)
        print("Nenhuma API_KEY configurada.")
        print("1. Cadastre-se grátis em https://www.football-data.org/client/register")
        print("2. Copie o token do seu painel")
        print("3. Cole em API_KEY no início de apostas_multiplas.py")
        print("=" * 60)
        sys.exit(1)

    print("Buscando jogos de hoje (fuso de São Paulo)...")
    jogos = obter_jogos_do_dia()
    if not jogos:
        print("Nenhum jogo encontrado para hoje nas competições do seu plano. Encerrando.")
        sys.exit(0)

    print(f"{len(jogos)} jogo(s) encontrado(s). Calculando forma recente dos times...")

    todas_candidatas = []
    for j in jogos:
        codigo = j.get("competition_code")
        todos_jogos_liga = obter_todos_jogos_competicao(codigo)
        nome_liga = j.get("competition_name", "Liga")
        candidatas = analisar_jogo(j, todos_jogos_liga, nome_liga)
        todas_candidatas.extend(candidatas)

        horario = unix_para_sp_str(j.get("date_unix"))
        print(f"  - [{nome_liga}] {j.get('home_name','?')} x {j.get('away_name','?')} ({horario})")

    if not todas_candidatas:
        print("Não foi possível calcular estatísticas suficientes para gerar sugestões "
              "(times sem jogos recentes suficientes na temporada atual).")
        sys.exit(0)

    print("Montando TOP do dia e múltiplas de 3/5/8 seleções...")
    top = montar_top(todas_candidatas)
    multiplas = montar_multiplas(todas_candidatas)

    print("Salvando sugestões no banco histórico...")
    salvar_sugestoes_no_banco(multiplas)

    relatorio = gerar_relatorio(top, multiplas)
    caminho = salvar_relatorio(relatorio)

    print(relatorio)
    print(f"\nRelatório salvo em: {caminho}")
    print(f"Histórico salvo em: {CAMINHO_BANCO}")


def main():
    parser = argparse.ArgumentParser(description="Gera sugestões diárias de apostas múltiplas via football-data.org.")
    parser.add_argument("--resultado", nargs=2, metavar=("ID", "RESULTADO"),
                         help="Registra o resultado real de uma sugestão salva no banco. "
                              "Ex: --resultado 42 green")
    parser.add_argument("--roi", action="store_true",
                         help="Mostra apenas o resumo de desempenho histórico (ROI) e sai.")
    args = parser.parse_args()

    if args.resultado:
        sugestao_id, resultado = args.resultado
        ok = atualizar_resultado(int(sugestao_id), resultado)
        print("Resultado registrado." if ok else "Sugestão não encontrada.")
        return

    if args.roi:
        print(calcular_roi_historico())
        return

    gerar_sugestoes_do_dia()


if __name__ == "__main__":
    main()
