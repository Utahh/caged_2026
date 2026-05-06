import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


st.set_page_config(
    page_title="Observatório Botucatu - Março 2026",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
        .block-container {padding-top: 1rem; padding-bottom: 1rem;}
        @media (max-width: 768px) {
            h1, h2, h3 {font-size: 1.1rem !important;}
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def formatar_inteiro_br(valor: float) -> str:
    return f"{valor:,.0f}".replace(",", ".")


def formatar_moeda_br(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def variacao_percentual_mom(atual: float, anterior: float) -> float:
    if anterior == 0:
        return 0.0 if atual == 0 else 100.0
    return ((atual - anterior) / abs(anterior)) * 100


@st.cache_data
def carregar_dados():
    base_dir = Path(__file__).resolve().parent
    pasta_dados = base_dir / "data"

    candidatos_caged = [
        base_dir / "relatorio_botucatu_q1_2026.csv",
        base_dir / "caged_botucatu_q1_2026.csv",
        pasta_dados / "relatorio_botucatu_q1_2026.csv",
        pasta_dados / "caged_botucatu_q1_2026.csv",
    ]
    candidatos_financas = [
        base_dir / "investimentos_botucatu_2026.csv",
        base_dir / "financas_botucatu_2026.csv",
        pasta_dados / "investimentos_botucatu_2026.csv",
        pasta_dados / "financas_botucatu_2026.csv",
    ]

    caminho_caged = next((str(p) for p in candidatos_caged if p.exists()), None)
    caminho_financas = next((str(p) for p in candidatos_financas if p.exists()), None)

    caged_existe = caminho_caged is not None
    financas_existe = caminho_financas is not None

    df_caged = (
        pd.read_csv(caminho_caged, sep=";", encoding="utf-8-sig")
        if caged_existe
        else pd.DataFrame()
    )
    df_financas = (
        pd.read_csv(
            caminho_financas,
            sep=";",
            encoding="utf-8-sig",
            decimal=",",
        )
        if financas_existe
        else pd.DataFrame()
    )

    return df_caged, df_financas, caged_existe, financas_existe


df_caged, df_financas, caged_existe, financas_existe = carregar_dados()

st.title("📊 Observatório Botucatu - Março 2026")
st.markdown("Relatório consolidado de emprego (CAGED) e finanças públicas (Siconfi).")
st.divider()

if not caged_existe and not financas_existe:
    st.warning(
        "⚠️ Os arquivos CSV não foram encontrados. "
        "Gere os dados primeiro e coloque os arquivos na mesma pasta do app."
    )
    st.stop()

if not caged_existe:
    st.warning(
        "⚠️ Arquivo ausente: `relatorio_botucatu_q1_2026.csv`. "
        "A seção de CAGED ficará indisponível."
    )

if not financas_existe:
    st.warning(
        "⚠️ Arquivo ausente: `investimentos_botucatu_2026.csv`. "
        "A seção financeira ficará indisponível."
    )


st.header("🏢 Relatório do CAGED (Março/2026 vs Fevereiro/2026)")

if caged_existe and not df_caged.empty:
    colunas_caged = {
        "mes_referencia",
        "secao",
        "subclasse",
        "saldomovimentacao",
        "admissao",
        "demissao",
    }

    if not colunas_caged.issubset(df_caged.columns):
        faltantes = sorted(colunas_caged - set(df_caged.columns))
        st.warning(f"⚠️ Colunas obrigatórias ausentes no CAGED: {', '.join(faltantes)}.")
    else:
        for coluna in ["mes_referencia", "saldomovimentacao", "admissao", "demissao"]:
            df_caged[coluna] = pd.to_numeric(df_caged[coluna], errors="coerce").fillna(0)

        df_mar = df_caged[df_caged["mes_referencia"] == 3].copy()
        df_fev = df_caged[df_caged["mes_referencia"] == 2].copy()

        if df_mar.empty or df_fev.empty:
            st.warning("⚠️ Não há dados suficientes para comparar Março e Fevereiro.")
        else:
            adm_mar = df_mar["admissao"].sum()
            des_mar = df_mar["demissao"].sum()
            saldo_mar = df_mar["saldomovimentacao"].sum()

            adm_fev = df_fev["admissao"].sum()
            des_fev = df_fev["demissao"].sum()
            saldo_fev = df_fev["saldomovimentacao"].sum()

            c1, c2, c3 = st.columns(3)
            c1.metric(
                "Total de Admissões (Março)",
                formatar_inteiro_br(adm_mar),
                f"{variacao_percentual_mom(adm_mar, adm_fev):+.1f}% vs Fev",
            )
            c2.metric(
                "Total de Desligamentos (Março)",
                formatar_inteiro_br(des_mar),
                f"{variacao_percentual_mom(des_mar, des_fev):+.1f}% vs Fev",
                delta_color="inverse",
            )
            c3.metric(
                "Saldo de Março",
                formatar_inteiro_br(saldo_mar),
                f"{variacao_percentual_mom(saldo_mar, saldo_fev):+.1f}% vs Fev",
            )

            st.write("---")

            st.subheader("Top 5 Seções com maior ganho e maior perda de saldo")
            saldo_secao = (
                df_mar.groupby("secao", as_index=False)["saldomovimentacao"]
                .sum()
                .sort_values("saldomovimentacao", ascending=False)
            )
            top_positivos = saldo_secao.head(5).copy()
            top_negativos = saldo_secao.sort_values("saldomovimentacao", ascending=True).head(5).copy()
            top_positivos["grupo"] = "Maiores ganhos"
            top_negativos["grupo"] = "Maiores perdas"
            top_secao = pd.concat([top_positivos, top_negativos], ignore_index=True)

            fig_secao = px.bar(
                top_secao,
                x="saldomovimentacao",
                y="secao",
                orientation="h",
                color="grupo",
                facet_col="grupo",
                labels={"saldomovimentacao": "Saldo", "secao": "Seção"},
                title="Saldo por Atividade Econômica (Seção) - Março/2026",
            )
            fig_secao.update_layout(showlegend=False, margin=dict(l=10, r=10, t=60, b=10))
            st.plotly_chart(fig_secao, use_container_width=True)

            st.subheader("Top 3 CNAEs (Subclasse)")
            col_a, col_b = st.columns(2)

            cnae_adm = (
                df_mar.groupby("subclasse", as_index=False)["admissao"]
                .sum()
                .sort_values("admissao", ascending=False)
                .head(3)
            )
            cnae_dem = (
                df_mar.groupby("subclasse", as_index=False)["demissao"]
                .sum()
                .sort_values("demissao", ascending=False)
                .head(3)
            )

            with col_a:
                st.markdown("**Top 3 que mais admitiram**")
                st.dataframe(cnae_adm, hide_index=True, use_container_width=True)

            with col_b:
                st.markdown("**Top 3 que mais demitiram**")
                st.dataframe(cnae_dem, hide_index=True, use_container_width=True)


st.header("💰 Relatório Financeiro (Investimentos em Banco)")

if financas_existe and not df_financas.empty:
    colunas_financas = {"Mes", "Codigo_Contabil", "Natureza", "Saldo_em_Reais"}

    if not colunas_financas.issubset(df_financas.columns):
        faltantes = sorted(colunas_financas - set(df_financas.columns))
        st.warning(f"⚠️ Colunas obrigatórias ausentes no financeiro: {', '.join(faltantes)}.")
    else:
        df_financas["Mes"] = pd.to_numeric(df_financas["Mes"], errors="coerce")
        df_financas["Saldo_em_Reais"] = pd.to_numeric(
            df_financas["Saldo_em_Reais"], errors="coerce"
        ).fillna(0)

        df_valid = df_financas.dropna(subset=["Mes"]).copy()
        if df_valid.empty:
            st.warning("⚠️ Não há dados válidos de mês no arquivo financeiro.")
        else:
            mes_recente = int(df_valid["Mes"].max())
            saldo_total = df_valid[df_valid["Mes"] == mes_recente]["Saldo_em_Reais"].sum()
            st.metric(
                label=f"Valor financeiro total mais recente (Mês {mes_recente}/2026)",
                value=formatar_moeda_br(saldo_total),
            )
