from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import streamlit as st

st.set_page_config(
    page_title="Dashboard Executivo Botucatu v2",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
        .stApp {background-color: #f1f5f9;}
        .block-container {padding-top: 0.55rem; max-width: 1400px; padding-bottom: 1.2rem;}
        div[data-testid="stMetric"] {
            background: #ffffff;
            border-radius: 12px;
            border: 1px solid #e5e7eb;
            padding: 0.45rem 0.65rem 0.55rem 0.65rem;
            box-shadow: 0 1px 4px rgba(15, 23, 42, 0.05);
        }
        div[data-testid="stMetricLabel"] p {
            font-size: 0.82rem !important;
            color: #64748b !important;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.55rem !important;
            line-height: 1.05 !important;
        }
        div[data-testid="stMetricDelta"] {
            font-size: 0.78rem !important;
        }
        .stDataFrame {background: #ffffff; border-radius: 12px; border: 1px solid #e5e7eb;}
        @media (max-width: 768px) {
            .block-container {padding-top: 0.35rem; padding-left: 0.6rem; padding-right: 0.6rem;}
            div[data-testid="column"] {
                width: 48% !important;
                flex: 1 1 48% !important;
                display: inline-block !important;
                min-width: 48% !important;
            }
            div[data-testid="stMetric"] {padding: 0.38rem 0.5rem 0.48rem 0.5rem;}
            div[data-testid="stMetricLabel"] p {font-size: 0.72rem !important;}
            div[data-testid="stMetricValue"] {font-size: 1.18rem !important;}
            div[data-testid="stMetricDelta"] {font-size: 0.67rem !important;}
            h1 {font-size: 1.35rem !important;}
            h2, h3 {font-size: 1.05rem !important;}
        }
    </style>
    """,
    unsafe_allow_html=True,
)

MESES_LABEL = {1: "Jan", 2: "Fev", 3: "Mar"}
SECAO_TO_GRANDE = {
    "A": "Agropecuária",
    "C": "Indústria",
    "F": "Construção",
    "G": "Comércio",
    "B": "Não Identificado",
    "D": "Não Identificado",
    "E": "Não Identificado",
}
POUPANCA_CODES = {"111310100", "111310200"}


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
        font=dict(size=13),
        title=dict(font=dict(size=16)),
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

    caged = pl.read_csv(caged_path, separator=";") if caged_path else pl.DataFrame()
    financas = pl.read_csv(fin_path, separator=";") if fin_path else pl.DataFrame()
    return caged, financas


def preparar_caged(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return df

    secao_expr = pl.col("secao").cast(pl.String).str.to_uppercase()
    grande_expr = (
        pl.when(secao_expr == "A")
        .then(pl.lit("Agropecuária"))
        .when(secao_expr == "C")
        .then(pl.lit("Indústria"))
        .when(secao_expr == "F")
        .then(pl.lit("Construção"))
        .when(secao_expr == "G")
        .then(pl.lit("Comércio"))
        .when(secao_expr.is_in(["H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U"]))
        .then(pl.lit("Serviços"))
        .otherwise(pl.lit("Não Identificado"))
    )

    atividade_nome_expr = (
        pl.when(pl.col("secao_descricao").is_not_null())
        .then(pl.col("secao_descricao").cast(pl.String))
        .otherwise(pl.col("secao").cast(pl.String))
    )

    return df.with_columns(
        [
            pl.col("mes_referencia").cast(pl.Int64),
            pl.col("admissao").cast(pl.Float64).fill_null(0.0),
            pl.col("demissao").cast(pl.Float64).fill_null(0.0),
            pl.col("saldomovimentacao").cast(pl.Float64).fill_null(0.0),
            pl.col("subclasse").cast(pl.String).str.zfill(7),
            atividade_nome_expr.alias("atividade_nome"),
            grande_expr.alias("grande_grupamento"),
        ]
    )


def preparar_financas(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return df

    col_saldo = "Saldo_em_Reais" if "Saldo_em_Reais" in df.columns else "Saldo em Reais"
    col_codigo = "Codigo_Contabil" if "Codigo_Contabil" in df.columns else "Código Contábil"
    col_mes = "Mes" if "Mes" in df.columns else "Mês"
    col_natureza = "Natureza"

    mapeamento_bancos = pl.DataFrame(
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

    return (
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
        .join(
            mapeamento_bancos.with_columns(pl.col("Codigo_Contabil").cast(pl.String)),
            on="Codigo_Contabil",
            how="left",
        )
        .with_columns(pl.col("Instituicao_Financeira").fill_null("Outros"))
    )


df_caged_raw, df_fin_raw = carregar_bases()
if df_caged_raw.is_empty() and df_fin_raw.is_empty():
    st.warning("⚠️ Arquivos CSV não encontrados. Verifique a raiz do projeto.")
    st.stop()

df_caged = preparar_caged(df_caged_raw)
df_fin = preparar_financas(df_fin_raw)

st.title("📊 Dashboard Executivo Botucatu v2")
st.caption("Painel de gestão pública com dados econômicos e financeiros de 2026")

with st.sidebar.expander("⚙️ Filtros CAGED", expanded=True):
    mes_options = [1, 2, 3]
    mes_sel = st.selectbox("Mês", mes_options, format_func=lambda m: MESES_LABEL.get(m, str(m)), index=2)
    atividades = (
        ["Todas"]
        + sorted(
            df_caged.select(pl.col("grande_grupamento").unique())
            .to_series()
            .drop_nulls()
            .to_list()
        )
        if not df_caged.is_empty()
        else ["Todas"]
    )
    atividade_sel = st.selectbox("Atividade Econômica", atividades, index=0)

if not df_caged.is_empty():
    df_caged_filtrado = df_caged.filter(pl.col("mes_referencia").is_in([1, 2, 3]))
    if atividade_sel != "Todas":
        df_caged_filtrado = df_caged_filtrado.filter(pl.col("grande_grupamento") == atividade_sel)

    mes_atual = df_caged_filtrado.filter(pl.col("mes_referencia") == mes_sel)
    mes_anterior = df_caged_filtrado.filter(pl.col("mes_referencia") == max(1, mes_sel - 1))

    adm = float(mes_atual.select(pl.col("admissao").sum()).item())
    des = float(mes_atual.select(pl.col("demissao").sum()).item())
    saldo = adm - des
    estoque = float(df_caged.select(pl.col("saldomovimentacao").sum()).item())
    adm_ant = float(mes_anterior.select(pl.col("admissao").sum()).item())
    des_ant = float(mes_anterior.select(pl.col("demissao").sum()).item())
    saldo_ant = adm_ant - des_ant

    c1, c2 = st.columns(2)
    c3, c4 = st.columns(2)
    c1.metric("Admissões", formatar_inteiro_br(adm), f"{variacao_percentual_mom(adm, adm_ant):+.1f}%")
    c2.metric("Desligamentos", formatar_inteiro_br(des), f"{variacao_percentual_mom(des, des_ant):+.1f}%", delta_color="inverse")
    c3.metric("Saldo", formatar_inteiro_br(saldo), f"{variacao_percentual_mom(saldo, saldo_ant):+.1f}%")
    c4.metric("Estoque", formatar_inteiro_br(estoque))

    evol = (
        df_caged_filtrado.group_by("mes_referencia")
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
    evol_pd = evol.to_pandas()

    st.markdown("## Evolução CAGED")
    fig_linhas = go.Figure()
    fig_linhas.add_trace(
        go.Scatter(
            x=evol_pd["mes_label"],
            y=evol_pd["admissao"],
            mode="lines+markers",
            name="Admissões",
            line=dict(color="#16a34a", width=3),
        )
    )
    fig_linhas.add_trace(
        go.Scatter(
            x=evol_pd["mes_label"],
            y=evol_pd["demissao"],
            mode="lines+markers",
            name="Desligamentos",
            line=dict(color="#dc2626", width=3),
        )
    )
    fig_linhas.update_layout(title="Evolução de Admissões e Desligamentos")
    estilizar_figura(fig_linhas)
    st.plotly_chart(fig_linhas, use_container_width=True, config=PLOTLY_CONFIG)

    fig_saldo = go.Figure()
    fig_saldo.add_trace(
        go.Bar(
            x=evol_pd["mes_label"],
            y=evol_pd["saldo"],
            marker_color=["#1d4ed8" if v >= 0 else "#b91c1c" for v in evol_pd["saldo"]],
            text=[formatar_inteiro_br(v) for v in evol_pd["saldo"]],
            textposition="outside",
            name="Saldo",
        )
    )
    fig_saldo.update_layout(title="Evolução do Saldo Mensal")
    estilizar_figura(fig_saldo)
    st.plotly_chart(fig_saldo, use_container_width=True, config=PLOTLY_CONFIG)

    st.markdown("## Top 5 CNAE 2.0 Subclasse")
    cnae_mes = (
        mes_atual.group_by("subclasse")
        .agg(
            [
                pl.col("admissao").sum().alias("Admissões"),
                pl.col("demissao").sum().alias("Desligamentos"),
                (pl.col("admissao").sum() - pl.col("demissao").sum()).alias("Saldo"),
            ]
        )
        .sort("Saldo", descending=True)
    )
    saldo_geral_mes = float(cnae_mes.select(pl.col("Saldo").sum()).item())
    cnae_mes = cnae_mes.with_columns(
        pl.when(pl.lit(saldo_geral_mes != 0))
        .then((pl.col("Saldo") / pl.lit(saldo_geral_mes)) * 100)
        .otherwise(pl.lit(0.0))
        .alias("% Impacto")
    )
    top_maiores = cnae_mes.head(5)
    top_menores = cnae_mes.tail(5).sort("Saldo")

    t1, t2 = st.columns(2)
    with t1:
        st.markdown("### Top 5 Maiores")
        st.dataframe(top_maiores.to_pandas(), use_container_width=True, hide_index=True, height=230)
    with t2:
        st.markdown("### Top 5 Menores")
        st.dataframe(top_menores.to_pandas(), use_container_width=True, hide_index=True, height=230)

st.markdown("## 💰 Módulo Financeiro")
if df_fin.is_empty():
    st.warning("⚠️ Base financeira indisponível.")
else:
    fin_3m = df_fin.filter(pl.col("Mes").is_in([1, 2, 3]))
    fin_m3 = fin_3m.filter(pl.col("Mes") == 3)

    saldo_total_m3 = float(fin_m3.select(pl.col("Saldo_em_Reais").sum()).item())
    st.metric("Saldo total (Mês 3)", formatar_moeda_br(saldo_total_m3))

    # Extrato bancário
    extrato = (
        fin_3m.select(["Mes", "Instituicao_Financeira", "Codigo_Contabil", "Saldo_em_Reais"])
        .sort(["Mes", "Saldo_em_Reais"], descending=[False, True])
    )
    st.markdown("### Extrato Bancário")
    st.dataframe(extrato.to_pandas(), use_container_width=True, hide_index=True, height=280)

    # Evolução histórica financeira
    evol_fin = (
        fin_3m.group_by("Mes")
        .agg(pl.col("Saldo_em_Reais").sum().alias("Total_Investido"))
        .sort("Mes")
        .with_columns(pl.col("Mes").replace_strict(MESES_LABEL).alias("Mes_Label"))
    )
    evol_fin_pd = evol_fin.to_pandas()
    fig_fin = px.area(
        evol_fin_pd,
        x="Mes_Label",
        y="Total_Investido",
        title="Evolução do valor total investido (Jan-Mar)",
    )
    fig_fin.update_traces(hovertemplate="<b>%{x}</b><br>Total: R$ %{y:,.2f}<extra></extra>")
    estilizar_figura(fig_fin)
    st.plotly_chart(fig_fin, use_container_width=True, config=PLOTLY_CONFIG)

    # Destaque poupança
    poupanca = fin_3m.filter(pl.col("Codigo_Contabil").is_in(list(POUPANCA_CODES)))
    total_poupanca = float(poupanca.select(pl.col("Saldo_em_Reais").sum()).item()) if not poupanca.is_empty() else 0.0
    por_banco_poupanca = (
        poupanca.group_by("Instituicao_Financeira")
        .agg(pl.col("Saldo_em_Reais").sum().alias("Valor"))
        .sort("Valor", descending=True)
    )
    bancos_top = (
        ", ".join(por_banco_poupanca.select("Instituicao_Financeira").to_series().to_list())
        if not por_banco_poupanca.is_empty()
        else "Sem dados de poupança"
    )
    st.metric("Total em Poupança (111310100 + 111310200)", formatar_moeda_br(total_poupanca))
    st.info(f"Maior volume de poupança concentrado em: **{bancos_top}**")
