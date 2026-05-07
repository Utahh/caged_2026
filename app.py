from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import streamlit as st

st.set_page_config(layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    """
    <style>
        :root {
            --bg-color: #f1f5f9;
            --card-color: #ffffff;
            --text-color: #0f172a;
            --muted-color: #475569;
            --border-color: #e2e8f0;
        }
        @media (prefers-color-scheme: dark) {
            :root {
                --bg-color: #0b1220;
                --card-color: #111827;
                --text-color: #e5e7eb;
                --muted-color: #cbd5e1;
                --border-color: #334155;
            }
        }
        .stApp { background-color: var(--bg-color); color: var(--text-color); }
        .block-container { padding-top: 0.5rem; max-width: 1400px; }
        h1, h2, h3, p, label, span, div { color: inherit; }
        div[data-testid="stMetric"] {
            background: var(--card-color);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 0.4rem 0.6rem 0.5rem 0.6rem;
        }
        div[data-testid="stMetricLabel"] p { color: var(--muted-color) !important; }
        div[data-testid="stMetricValue"] { color: var(--text-color) !important; }
        div[data-testid="stMetricDelta"] { color: inherit !important; }
        div[data-testid="stDataFrame"] {
            background: var(--card-color);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
        }
        @media (max-width: 768px) {
            div[data-testid="column"] {
                width: 48% !important;
                flex: 1 1 48% !important;
                display: inline-block !important;
                min-width: 48% !important;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

MESES = {1: "Janeiro", 2: "Fevereiro", 3: "Março"}
POUPANCA_CODIGOS = ["111310100", "111310200"]


def br_int(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def br_money(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def mom(atual: float, anterior: float) -> float:
    if anterior == 0:
        return 0.0 if atual == 0 else 100.0
    return ((atual - anterior) / abs(anterior)) * 100


def path_exist(paths: list[Path]) -> Path | None:
    return next((p for p in paths if p.exists()), None)


@st.cache_data
def load_data() -> tuple[pl.DataFrame, pl.DataFrame]:
    root = Path(__file__).resolve().parent
    data_dir = root / "data"
    caged_path = path_exist(
        [
            root / "relatorio_botucatu_q1_2026.csv",
            root / "caged_botucatu_q1_2026.csv",
            data_dir / "relatorio_botucatu_q1_2026.csv",
            data_dir / "caged_botucatu_q1_2026.csv",
        ]
    )
    fin_path = path_exist(
        [
            root / "investimentos_botucatu_2026.csv",
            root / "financas_botucatu_2026.csv",
            data_dir / "investimentos_botucatu_2026.csv",
            data_dir / "financas_botucatu_2026.csv",
        ]
    )
    caged = pl.read_csv(caged_path, separator=";") if caged_path else pl.DataFrame()
    fin = pl.read_csv(fin_path, separator=";") if fin_path else pl.DataFrame()
    return caged, fin


def normalize_caged(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return df
    section = pl.col("secao").cast(pl.String).str.to_uppercase()
    grande = (
        pl.when(section == "C")
        .then(pl.lit("Indústria"))
        .when(section == "G")
        .then(pl.lit("Comércio"))
        .when(section == "F")
        .then(pl.lit("Construção"))
        .when(section == "A")
        .then(pl.lit("Agropecuária"))
        .when(section.is_in(["H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U"]))
        .then(pl.lit("Serviços"))
        .otherwise(pl.lit("Não Identificado"))
    )
    atividade = (
        pl.when(pl.col("subclasse_descricao").is_not_null())
        .then(pl.col("subclasse_descricao").cast(pl.String))
        .otherwise(pl.lit("Não Identificado"))
    )
    return df.with_columns(
        [
            pl.col("mes_referencia").cast(pl.Int64),
            pl.col("admissao").cast(pl.Float64).fill_null(0.0),
            pl.col("demissao").cast(pl.Float64).fill_null(0.0),
            pl.col("saldomovimentacao").cast(pl.Float64).fill_null(0.0),
            pl.col("subclasse").cast(pl.String).str.zfill(7),
            grande.alias("Grande Grupo"),
            atividade.alias("Atividade Econômica"),
        ]
    )


def normalize_fin(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return df
    saldo_col = "Saldo_em_Reais" if "Saldo_em_Reais" in df.columns else "Saldo em Reais"
    cod_col = "Codigo_Contabil" if "Codigo_Contabil" in df.columns else "Código Contábil"
    mes_col = "Mes" if "Mes" in df.columns else "Mês"
    mapa = pl.DataFrame(
        {
            "Codigo_Contabil": [
                "111110100",
                "111110200",
                "111110603",
                "111110604",
                "111111900",
                "111115000",
                "111310100",
                "111310200",
            ],
            "Instituição Financeira": [
                "Caixa da Prefeitura",
                "Banco do Brasil",
                "Caixa Econômica Federal",
                "Santander / Outros",
                "Fundos de Investimento",
                "Aplicações em Renda Fixa",
                "Poupança Municipal",
                "Poupança Vinculada",
            ],
        }
    )
    return (
        df.with_columns(
            [
                pl.col(cod_col).cast(pl.String).alias("Codigo_Contabil"),
                pl.col(mes_col).cast(pl.Int64).alias("Mes"),
                pl.col(saldo_col)
                .cast(pl.String)
                .str.replace(",", ".")
                .cast(pl.Float64, strict=False)
                .fill_null(0.0)
                .alias("Saldo"),
            ]
        )
        .join(mapa, on="Codigo_Contabil", how="left")
        .with_columns(pl.col("Instituição Financeira").fill_null("Outros"))
    )


caged_raw, fin_raw = load_data()
if caged_raw.is_empty() and fin_raw.is_empty():
    st.warning("⚠️ Arquivos não encontrados na raiz do projeto.")
    st.stop()

caged = normalize_caged(caged_raw)
fin = normalize_fin(fin_raw)

st.title("📊 Dashboard Executivo Março 2026")

with st.sidebar.expander("Filtros", expanded=True):
    month = st.selectbox("Mês", [1, 2, 3], format_func=lambda m: MESES[m], index=2)
    grupos = ["Todos"] + sorted(caged.select(pl.col("Grande Grupo").unique()).to_series().drop_nulls().to_list()) if not caged.is_empty() else ["Todos"]
    grupo = st.selectbox("Atividade Econômica", grupos, index=0)

if not caged.is_empty():
    c = caged.filter(pl.col("mes_referencia").is_in([1, 2, 3]))
    if grupo != "Todos":
        c = c.filter(pl.col("Grande Grupo") == grupo)

    c_month = c.filter(pl.col("mes_referencia") == month)
    c_prev = c.filter(pl.col("mes_referencia") == max(1, month - 1))

    adm = float(c_month.select(pl.col("admissao").sum()).item())
    des = float(c_month.select(pl.col("demissao").sum()).item())
    saldo = adm - des
    estoque = float(c.select(pl.col("saldomovimentacao").sum()).item())
    adm_prev = float(c_prev.select(pl.col("admissao").sum()).item())
    des_prev = float(c_prev.select(pl.col("demissao").sum()).item())
    saldo_prev = adm_prev - des_prev

    k1, k2 = st.columns(2)
    k3, k4 = st.columns(2)
    k1.metric("Admissões", br_int(adm), f"{mom(adm, adm_prev):+.1f}%")
    k2.metric("Desligamentos", br_int(des), f"{mom(des, des_prev):+.1f}%", delta_color="inverse")
    k3.metric("Saldo", br_int(saldo), f"{mom(saldo, saldo_prev):+.1f}%")
    k4.metric("Estoque", br_int(estoque))

    monthly = (
        c.group_by("mes_referencia")
        .agg(
            [
                pl.col("admissao").sum().alias("Admissões"),
                pl.col("demissao").sum().alias("Desligamentos"),
                (pl.col("admissao").sum() - pl.col("demissao").sum()).alias("Saldo"),
            ]
        )
        .sort("mes_referencia")
        .with_columns(pl.col("mes_referencia").replace_strict(MESES).alias("Mês"))
    )
    monthly_pd = monthly.to_pandas()

    st.markdown("## Evolução Mensal CAGED")
    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(x=monthly_pd["Mês"], y=monthly_pd["Admissões"], mode="lines+markers", name="Admissões"))
    fig_line.add_trace(go.Scatter(x=monthly_pd["Mês"], y=monthly_pd["Desligamentos"], mode="lines+markers", name="Desligamentos"))
    fig_line.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", title="Admissões x Desligamentos")
    st.plotly_chart(fig_line, theme="streamlit", use_container_width=True)

    fig_bar = px.bar(monthly_pd, x="Mês", y="Saldo", title="Evolução do Saldo Mensal")
    fig_bar.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig_bar, theme="streamlit", use_container_width=True)

    st.markdown("## Top 5 Atividades (Março)")
    rank = (
        c.filter(pl.col("mes_referencia") == 3)
        .group_by("Atividade Econômica")
        .agg(
            [
                pl.col("admissao").sum().alias("Admissões"),
                pl.col("demissao").sum().alias("Desligamentos"),
                (pl.col("admissao").sum() - pl.col("demissao").sum()).alias("Saldo"),
            ]
        )
        .sort("Saldo", descending=True)
    )
    total_saldo = float(rank.select(pl.col("Saldo").sum()).item())
    rank = rank.with_columns(
        pl.when(pl.lit(total_saldo != 0))
        .then((pl.col("Saldo") / pl.lit(total_saldo)) * 100)
        .otherwise(pl.lit(0.0))
        .alias("% Impacto")
    )
    top_maiores = rank.head(5)
    top_menores = rank.tail(5).sort("Saldo")

    a1, a2 = st.columns(2)
    with a1:
        st.markdown("### Maiores Saldos")
        st.dataframe(top_maiores.to_pandas(), use_container_width=True, hide_index=True)
    with a2:
        st.markdown("### Menores Saldos")
        st.dataframe(top_menores.to_pandas(), use_container_width=True, hide_index=True)

st.markdown("## 💰 Módulo Financeiro")
if fin.is_empty():
    st.warning("⚠️ Base financeira indisponível.")
else:
    fin_3 = fin.filter(pl.col("Mes").is_in([1, 2, 3]))
    extrato = (
        fin_3.select(["Mes", "Instituição Financeira", "Saldo"])
        .sort(["Mes", "Saldo"], descending=[False, True])
        .with_columns(
            [
                pl.col("Mes").replace_strict(MESES).alias("Mês"),
                pl.col("Saldo").map_elements(br_money, return_dtype=pl.String),
            ]
        )
        .select(["Mês", "Instituição Financeira", "Saldo"])
    )
    st.markdown("### Extrato Bancário")
    st.dataframe(extrato.to_pandas(), use_container_width=True, hide_index=True)

    evol_fin = (
        fin_3.group_by("Mes")
        .agg(pl.col("Saldo").sum().alias("Saldo Total"))
        .sort("Mes")
        .with_columns(pl.col("Mes").replace_strict(MESES).alias("Mês"))
    )
    evol_fin_pd = evol_fin.to_pandas()
    fig_fin = px.line(evol_fin_pd, x="Mês", y="Saldo Total", markers=True, title="Evolução Histórica do Saldo Total")
    fig_fin.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig_fin, theme="streamlit", use_container_width=True)

    poup = fin_3.filter(pl.col("Codigo_Contabil").is_in(POUPANCA_CODIGOS))
    total_poup = float(poup.select(pl.col("Saldo").sum()).item()) if not poup.is_empty() else 0.0
    poup_bancos = (
        poup.group_by("Instituição Financeira")
        .agg(pl.col("Saldo").sum().alias("Saldo"))
        .sort("Saldo", descending=True)
    )
    bancos_txt = ", ".join(poup_bancos.select("Instituição Financeira").to_series().to_list()) if not poup_bancos.is_empty() else "Sem dados"
    st.metric("Total investido em Poupança", br_money(total_poup))
    st.info(f"Maior volume em poupança está em: **{bancos_txt}**")
