
import sqlite3
from pathlib import Path
import pandas as pd
import streamlit as st

DB = Path("footystats_history.db")

st.set_page_config(page_title="FootyStats Analyzer V3", page_icon="⚽", layout="wide")

REQUIRED = ["Data","Liga","Jogo","Forma_Casa","Forma_Fora","Odd_Casa","Odd_Empate","Odd_Fora"]

def db():
    con = sqlite3.connect(DB)
    con.execute("""
    CREATE TABLE IF NOT EXISTS previsoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data TEXT, liga TEXT, jogo TEXT, escolha TEXT, lado TEXT,
        score REAL, odd REAL, prob_mercado REAL,
        resultado TEXT DEFAULT NULL,
        acertou INTEGER DEFAULT NULL
    )""")
    con.commit()
    return con

def score_game(r, side):
    form = r["Forma_Casa"] if side=="Casa" else r["Forma_Fora"]
    opp = r["Forma_Fora"] if side=="Casa" else r["Forma_Casa"]
    odd = r["Odd_Casa"] if side=="Casa" else r["Odd_Fora"]
    if any(pd.isna(x) for x in [form,opp,odd,r["Odd_Casa"],r["Odd_Empate"],r["Odd_Fora"]]) or odd <= 1:
        return None
    probs = [1/r["Odd_Casa"],1/r["Odd_Empate"],1/r["Odd_Fora"]]
    market = (1/odd)/sum(probs)
    form_signal = max(0,min(1,form/max(form+opp,1e-9)))
    raw = .55*form_signal + .45*market
    if form <= opp: raw *= .90
    return round(max(0,min(100,raw*100)),1), market

def analyze(df):
    rows=[]
    for _,r in df.iterrows():
        teams=str(r["Jogo"]).split(" x ")
        for side in ("Casa","Fora"):
            x=score_game(r,side)
            if not x: continue
            score,market=x
            choice=teams[0] if side=="Casa" else (teams[-1] if len(teams)>1 else "Visitante")
            odd=r["Odd_Casa"] if side=="Casa" else r["Odd_Fora"]
            rows.append({
                "Data":r["Data"],"Liga":r["Liga"],"Jogo":r["Jogo"],
                "Escolha":("🏠 " if side=="Casa" else "✈️ ")+choice,
                "Lado":side,"Score":score,"Odd":odd,
                "Prob. mercado %":round(market*100,1)
            })
    return pd.DataFrame(rows).sort_values(["Score","Odd"],ascending=[False,True]) if rows else pd.DataFrame()

def cls(x):
    return "🔥 Forte" if x>=85 else "🟢 Bom" if x>=80 else "🟡 Moderado" if x>=75 else "🔴 Descartar"

st.title("⚽ FootyStats Analyzer V3")
st.caption("Triagem estatística e backtest. Não é garantia de resultado.")

st.sidebar.header("Dados")
file=st.sidebar.file_uploader("CSV",type="csv")
demo=pd.DataFrame([
["2026-08-07","Exemplo","Time A x Time B",1.50,.80,1.55,4,6],
["2026-08-07","Exemplo","Time C x Time D",1.20,1,1.75,3.6,4.5],
],columns=REQUIRED)
df=pd.read_csv(file) if file else demo
missing=[c for c in REQUIRED if c not in df.columns]
if missing:
    st.error("Colunas ausentes: "+", ".join(missing)); st.stop()
for c in REQUIRED[3:]: df[c]=pd.to_numeric(df[c],errors="coerce")

ranking=analyze(df)
if ranking.empty:
    st.warning("Sem dados suficientes para análise.")
else:
    ranking["Classificação"]=ranking["Score"].apply(cls)
    st.subheader("🏆 TOP 10")
    st.dataframe(ranking.head(10),use_container_width=True,hide_index=True)

    approved=ranking[ranking.Score>=80].drop_duplicates("Jogo")
    st.subheader("🎯 Múltiplas")
    cols=st.columns(3)
    for col,(title,n) in zip(cols,[("🟢 Conservadora",3),("🟡 Equilibrada",5),("🔴 Agressiva",8)]):
        with col:
            slip=approved.head(n)
            st.markdown(f"**{title}**")
            if slip.empty:
                st.write("NÃO APOSTAR")
            else:
                st.write(" • ".join(slip.Escolha))
                st.metric("Odd combinada",f"{slip.Odd.prod():.2f}")
                st.metric("Score médio",f"{slip.Score.mean():.1f}")

con=db()

st.divider()
st.subheader("💾 Registrar previsões")
st.write("Registre as previsões antes dos jogos. Depois, informe o resultado para o backtest.")
if not ranking.empty and st.button("Registrar TOP 10 no histórico"):
    rows=[]
    for _,r in ranking.head(10).iterrows():
        rows.append((str(r.Data),str(r.Liga),str(r.Jogo),str(r.Escolha),str(r.Lado),
                     float(r.Score),float(r.Odd),float(r["Prob. mercado %"])/100))
    con.executemany("""INSERT INTO previsoes
    (data,liga,jogo,escolha,lado,score,odd,prob_mercado)
    VALUES (?,?,?,?,?,?,?,?)""",rows)
    con.commit()
    st.success(f"{len(rows)} previsões registradas.")

hist=pd.read_sql_query("SELECT * FROM previsoes ORDER BY id DESC",con)
if not hist.empty:
    st.subheader("📚 Backtest")
    st.write("Edite o CSV abaixo com os resultados reais ou use o painel de resultados na próxima etapa.")
    st.dataframe(hist,use_container_width=True,hide_index=True)

    done=hist[hist["acertou"].notna()]
    if not done.empty:
        hit=done["acertou"].mean()*100
        st.metric("Taxa de acerto",f"{hit:.1f}%")
        by=done.assign(faixa=pd.cut(done.score,[0,74.999,79.999,84.999,100],
                                    labels=["<75","75-79","80-84","85-100"]))
        stats=by.groupby("faixa",observed=True)["acertou"].mean().mul(100).round(1).reset_index(name="Acerto %")
        st.dataframe(stats,use_container_width=True,hide_index=True)

st.subheader("📥 Modelo de dados")
st.download_button("Baixar modelo CSV",df.to_csv(index=False).encode("utf-8-sig"),
                   "modelo_footystats_v3.csv","text/csv")

con.close()
