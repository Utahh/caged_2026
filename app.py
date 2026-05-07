from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import streamlit as st

st.set_page_config(
    page_title="Observatório Botucatu - Março 2026",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
        .block-container {padding-top: 0.6rem; max-width: 1400px;}
        @media (max-width: 768px) {
            div[data-testid="column"] {
                width: 48% !important;
                flex: 1 1 48% !important;
                display: inline-block !important;
                min-width: 48% !important;
            }
            h1 {font-size: 1.35rem !important;}
            h2, h3 {font-size: 1.05rem !important;}
        }
    </style>
    """,
    unsafe_allow_html=True,
)

MESES_LABEL = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}


def formatar_inteiro_br(valor: float) -> str:
    return f"{valor:,.0f}".replace(",", ".")


def formatar_moeda_br(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def variacao_percentual_mom(atual: float, anterior: float) -> float:
    if anterior == 0:
        return 0.0 if atual == 0 else 100.0
    return ((atual - anterior) / abs(anterior)) * 100


def estilizar_figura(fig):
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(bgcolor="#111827", font=dict(color="white", size=13)),
        margin=dict(l=10, r=10, t=60, b=20),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, fixedrange=True)
    fig.update_yaxes(showgrid=False, zeroline=False, fixedrange=True)
    return fig


PLOTLY_CONFIG = {
    "responsive": True,
    "scrollZoom": False,
    "doubleClick": False,
    "displaylogo": False,
    "modeBarButtonsToRemove": [
        "zoom2d",
        "pan2d",
        "select2d",
        "lasso2d",
        "zoomIn2d",
        "zoomOut2d",
        "autoScale2d",
        "resetScale2d",
    ],
}


def primeiro_existente(candidatos: list[Path]) -> Path | None:
    return next((p for p in candidatos if p.exists()), None)


@st.cache_data
def carregar_bases() -> tuple[pl.DataFrame, pl.DataFrame]:
    base = Path(__file__).resolve().parent
    data = base / "data"

    caged_path = primeiro_existente(
        [
            base / "relatorio_botucatu_q1_2026.csv",
            base / "caged_botucatu_q1_2026.csv",
            data / "relatorio_botucatu_q1_2026.csv",
            data / "caged_botucatu_q1_2026.csv",
        ]
    )
    fin_path = primeiro_existente(
        [
            base / "investimentos_botucatu_2026.csv",
            base / "financas_botucatu_2026.csv",
            data / "investimentos_botucatu_2026.csv",
            data / "financas_botucatu_2026.csv",
        ]
    )

    if caged_path is None and fin_path is None:
        return pl.DataFrame(), pl.DataFrame()

    caged = pl.read_csv(caged_path, separator=";") if caged_path else pl.DataFrame()
    financas = pl.read_csv(fin_path, separator=";") if fin_path else pl.DataFrame()
    return caged, financas


def preparar_financas(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return df

    col_saldo = "Saldo_em_Reais" if "Saldo_em_Reais" in df.columns else "Saldo em Reais"
    col_codigo = "Codigo_Contabil" if "Codigo_Contabil" in df.columns else "Código Contábil"
    col_natureza = "Natureza"
    col_mes = "Mes" if "Mes" in df.columns else "Mês"

    mapa_instituicoes = pl.DataFrame(
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
            "Instituicao_Financeira": [
                "Caixa da Prefeitura",
                "Banco do Brasil (Conta Única)",
                "Caixa Econômica Federal (Movimento)",
                "Banco Santander / Outros Bancos",
                "Fundos de Investimento (Liquidez)",
                "Aplicações em Renda Fixa",
                "Poupança",
                "Poupança Vinculada",
            ],
        }
    )

    out = (
        df.with_columns(
            [
                pl.col(col_codigo).cast(pl.String).alias("Codigo_Contabil"),
                pl.col(col_mes).cast(pl.Int64).alias("Mes"),
                pl.col(col_natureza).cast(pl.String).alias("Natureza"),
                pl.col(col_saldo)
                .cast(pl.String)
                .str.replace(",", ".")
                .cast(pl.Float64, strict=False)
                .fill_null(0.0)
                .alias("Saldo_em_Reais"),
            ]
        )
        .join(mapa_instituicoes, on="Codigo_Contabil", how="left")
        .with_columns(pl.col("Instituicao_Financeira").fill_null("Outros"))
    )
    return out


def preparar_caged(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return df

    cols = df.columns
    secao_desc = "secao_descricao" if "secao_descricao" in cols else None

    out = df.with_columns(
        [
            pl.col("mes_referencia").cast(pl.Int64),
            pl.col("admissao").cast(pl.Float64).fill_null(0),
            pl.col("demissao").cast(pl.Float64).fill_null(0),
            pl.col("saldomovimentacao").cast(pl.Float64).fill_null(0),
            pl.col("subclasse").cast(pl.String).str.zfill(7),
            pl.when(pl.lit(secao_desc is not None))
            .then(pl.col(secao_desc).cast(pl.String))
            .otherwise(pl.col("secao").cast(pl.String))
            .alias("atividade_nome"),
        ]
    )
    return out


df_caged_raw, df_fin_raw = carregar_bases()
if df_caged_raw.is_empty() and df_fin_raw.is_empty():
    st.warning("⚠️ Arquivos CSV não encontrados.")
    st.stop()

df_caged = preparar_caged(df_caged_raw)
df_fin = preparar_financas(df_fin_raw)

st.title("📊 Observatório Econômico - Botucatu/SP")
st.caption("Indicadores de empregabilidade e finanças públicas — 2026")

# =========================
# KPIs CAGED (M3 vs M2)
# =========================
if not df_caged.is_empty():
    m3 = df_caged.filter(pl.col("mes_referencia") == 3)
    m2 = df_caged.filter(pl.col("mes_referencia") == 2)

    adm_m3 = float(m3.select(pl.col("admissao").sum()).item())
    des_m3 = float(m3.select(pl.col("demissao").sum()).item())
    adm_m2 = float(m2.select(pl.col("admissao").sum()).item())
    des_m2 = float(m2.select(pl.col("demissao").sum()).item())

    saldo_m3 = adm_m3 - des_m3
    saldo_m2 = adm_m2 - des_m2
    estoque = float(df_caged.select(pl.col("saldomovimentacao").sum()).item())

    c1, c2 = st.columns(2)
    c3, c4 = st.columns(2)
    c1.metric("Admissões", formatar_inteiro_br(adm_m3), f"{variacao_percentual_mom(adm_m3, adm_m2):+.1f}%")
    c2.metric(
        "Desligamentos",
        formatar_inteiro_br(des_m3),
        f"{variacao_percentual_mom(des_m3, des_m2):+.1f}%",
        delta_color="inverse",
    )
    c3.metric("Saldo", formatar_inteiro_br(saldo_m3), f"{variacao_percentual_mom(saldo_m3, saldo_m2):+.1f}%")
    c4.metric("Estoque", formatar_inteiro_br(estoque))

    st.markdown("## Evolução CAGED")
    evol = (
        df_caged.group_by("mes_referencia")
        .agg(
            [
                pl.col("admissao").sum().alias("admissao"),
                pl.col("demissao").sum().alias("demissao"),
                (pl.col("admissao").sum() - pl.col("demissao").sum()).alias("saldo"),
            ]
        )
        .sort("mes_referencia")
        .with_columns(pl.col("mes_referencia").replace_strict(MESES_LABEL).alias("mes_label"))
    )

    fig_evol = go.Figure()
    evol_pd = evol.to_pandas()
    fig_evol.add_trace(
        go.Bar(
            x=evol_pd["mes_label"],
            y=evol_pd["saldo"],
            name="Saldo",
            marker_color="rgba(26,35,126,0.35)",
            hovertemplate="<b>%{x}</b><br>Saldo: %{y:,.0f}<extra></extra>",
        )
    )
    fig_evol.add_trace(
        go.Scatter(
            x=evol_pd["mes_label"],
            y=evol_pd["admissao"],
            mode="lines+markers",
            name="Admissões",
            line=dict(color="#16a34a", width=3),
            hovertemplate="<b>%{x}</b><br>Admissões: %{y:,.0f}<extra></extra>",
        )
    )
    fig_evol.add_trace(
        go.Scatter(
            x=evol_pd["mes_label"],
            y=evol_pd["demissao"],
            mode="lines+markers",
            name="Desligamentos",
            line=dict(color="#dc2626", width=3),
            hovertemplate="<b>%{x}</b><br>Desligamentos: %{y:,.0f}<extra></extra>",
        )
    )
    fig_evol.update_layout(title="Evolução histórica — admissões, desligamentos e saldo", barmode="overlay")
    estilizar_figura(fig_evol)
    st.plotly_chart(fig_evol, use_container_width=True, config=PLOTLY_CONFIG)

    st.markdown("## Atividades Econômicas")
    atividade = (
        m3.group_by("atividade_nome")
        .agg((pl.col("admissao").sum() - pl.col("demissao").sum()).alias("saldo"))
        .sort("saldo", descending=True)
    )
    top5 = atividade.head(5).with_columns(pl.lit("Top 5 Maiores").alias("grupo"))
    bot5 = atividade.tail(5).sort("saldo").with_columns(pl.lit("Top 5 Piores").alias("grupo"))
    tops = pl.concat([top5, bot5])
    tops_pd = tops.to_pandas()
    cores = tops_pd["grupo"].map({"Top 5 Maiores": "#16a34a", "Top 5 Piores": "#dc2626"})

    fig_top = go.Figure()
    fig_top.add_trace(
        go.Bar(
            x=tops_pd["saldo"],
            y=tops_pd["atividade_nome"],
            orientation="h",
            marker_color=cores,
            text=tops_pd["saldo"].map(lambda x: formatar_inteiro_br(float(x))),
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>Saldo: %{x:,.0f}<extra></extra>",
        )
    )
    fig_top.update_layout(title="Top 5 maiores e piores saldos por atividade", showlegend=False)
    estilizar_figura(fig_top)
    st.plotly_chart(fig_top, use_container_width=True, config=PLOTLY_CONFIG)

    if not top5.is_empty():
        campea = top5.row(0)
        st.info(f"🏆 Atividade campeã em março: **{campea[0]}** com saldo **{formatar_inteiro_br(campea[1])}**.")

# =========================
# Módulo Financeiro
# =========================
st.markdown("## 💰 Módulo Financeiro")
if df_fin.is_empty():
    st.warning("⚠️ Base financeira indisponível.")
else:
    fin_m3 = df_fin.filter(pl.col("Mes") == 3)
    saldo_total = float(fin_m3.select(pl.col("Saldo_em_Reais").sum()).item())
    st.metric("Saldo total em investimentos (Mês 3)", formatar_moeda_br(saldo_total))

    donut = (
        fin_m3.group_by("Instituicao_Financeira")
        .agg(pl.col("Saldo_em_Reais").sum().alias("saldo"))
        .sort("saldo", descending=True)
    )
    donut_pd = donut.to_pandas()
    fig_donut = px.pie(
        donut_pd,
        names="Instituicao_Financeira",
        values="saldo",
        hole=0.6,
        title="Distribuição do saldo por instituição financeira (Mês 3)",
    )
    fig_donut.update_traces(
        textposition="inside",
        textinfo="percent+label",
        hovertemplate="<b>%{label}</b><br>Saldo: R$ %{value:,.2f}<extra></extra>",
    )
    estilizar_figura(fig_donut)
    st.plotly_chart(fig_donut, use_container_width=True, config=PLOTLY_CONFIG)
