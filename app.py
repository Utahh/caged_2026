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

st.title("📊 Dados gerais de março de 2026")
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


st.header("🏢 Relatório do CAGED")

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

        df_q1 = df_caged[df_caged["mes_referencia"].isin([1, 2, 3])].copy()
        df_mar = df_q1[df_q1["mes_referencia"] == 3].copy()
        df_fev = df_q1[df_q1["mes_referencia"] == 2].copy()

        if df_mar.empty or df_fev.empty:
            st.warning("⚠️ Não há dados suficientes para comparar Março e Fevereiro.")
        else:
            adm_mar = df_mar["admissao"].sum()
            des_mar = df_mar["demissao"].sum()
            saldo_mar = df_mar["saldomovimentacao"].sum()
            if "estoque_anual_2026" in df_caged.columns:
                estoque_anual = (
                    df_caged[["secao", "subclasse", "estoque_anual_2026"]]
                    .drop_duplicates()
                    ["estoque_anual_2026"]
                    .sum()
                )
            else:
                estoque_anual = df_caged[df_caged["mes_referencia"].isin([1, 2, 3])]["saldomovimentacao"].sum()

            adm_fev = df_fev["admissao"].sum()
            des_fev = df_fev["demissao"].sum()
            saldo_fev = df_fev["saldomovimentacao"].sum()

            c1, c2, c3, c4 = st.columns(4)
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
            c4.metric("Estoque Anual 2026 (Jan-Mar)", formatar_inteiro_br(estoque_anual))

            st.write("---")
            st.subheader("Comparativo dos indicadores nos 3 meses (Jan-Mar)")
            comparativo = (
                df_q1.groupby("mes_referencia", as_index=False)[
                    ["admissao", "demissao", "saldomovimentacao"]
                ]
                .sum()
                .sort_values("mes_referencia")
            )
            comparativo_long = comparativo.melt(
                id_vars="mes_referencia",
                value_vars=["admissao", "demissao", "saldomovimentacao"],
                var_name="indicador",
                value_name="quantidade",
            )
            nomes_indicador = {
                "admissao": "Admissões",
                "demissao": "Desligamentos",
                "saldomovimentacao": "Saldo",
            }
            comparativo_long["indicador"] = comparativo_long["indicador"].map(nomes_indicador)
            comparativo_long["mes_label"] = comparativo_long["mes_referencia"].map({1: "Jan", 2: "Fev", 3: "Mar"})
            fig_comp = px.bar(
                comparativo_long,
                x="mes_label",
                y="quantidade",
                color="indicador",
                barmode="group",
                labels={"mes_label": "Mês", "quantidade": "Quantidade", "indicador": ""},
                title="Comparativo mensal de admissões, desligamentos e saldo",
            )
            st.plotly_chart(fig_comp, use_container_width=True)

            st.subheader("Atividades econômicas em cluster (Março)")
            if "secao_descricao" in df_mar.columns:
                df_mar["secao_label"] = df_mar["secao_descricao"].fillna(df_mar["secao"]).astype(str).str.strip()
            else:
                df_mar["secao_label"] = df_mar["secao"].astype(str).str.strip()

            cluster_secao = (
                df_mar.groupby("secao_label", as_index=False)[["admissao", "demissao", "saldomovimentacao"]]
                .sum()
                .sort_values("saldomovimentacao", ascending=False)
            )
            cluster_long = cluster_secao.melt(
                id_vars="secao_label",
                value_vars=["admissao", "demissao", "saldomovimentacao"],
                var_name="indicador",
                value_name="quantidade",
            )
            cluster_long["indicador"] = cluster_long["indicador"].map(nomes_indicador)
            fig_cluster = px.bar(
                cluster_long,
                x="secao_label",
                y="quantidade",
                color="indicador",
                barmode="group",
                labels={"secao_label": "Atividade Econômica", "quantidade": "Quantidade", "indicador": ""},
                title="Cluster por atividade econômica",
            )
            fig_cluster.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig_cluster, use_container_width=True)

            st.subheader("Top 5 atividades com maior e menor saldo (Março)")
            saldo_secao = (
                df_mar.groupby("secao_label", as_index=False)["saldomovimentacao"]
                .sum()
                .sort_values("saldomovimentacao", ascending=False)
            )
            col_pos, col_neg = st.columns(2)
            with col_pos:
                st.markdown("**Top 5 maiores saldos**")
                st.dataframe(
                    saldo_secao.head(5).rename(
                        columns={"secao_label": "Atividade Econômica", "saldomovimentacao": "Saldo"}
                    ),
                    hide_index=True,
                    use_container_width=True,
                )
            with col_neg:
                st.markdown("**Top 5 menores saldos**")
                st.dataframe(
                    saldo_secao.sort_values("saldomovimentacao", ascending=True)
                    .head(5)
                    .rename(columns={"secao_label": "Atividade Econômica", "saldomovimentacao": "Saldo"}),
                    hide_index=True,
                    use_container_width=True,
                )

            st.subheader("Top 3 CNAEs (Subclasse)")
            col_a, col_b = st.columns(2)

            cnae_adm = (
                df_mar.groupby(
                    [c for c in ["subclasse", "subclasse_descricao"] if c in df_mar.columns],
                    as_index=False,
                )["admissao"]
                .sum()
                .sort_values("admissao", ascending=False)
                .head(3)
            )
            cnae_dem = (
                df_mar.groupby(
                    [c for c in ["subclasse", "subclasse_descricao"] if c in df_mar.columns],
                    as_index=False,
                )["demissao"]
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
            df_valid["instituicao_financeira"] = (
                "Conta "
                + df_valid["Codigo_Contabil"].astype(str).str.strip()
                + " ("
                + df_valid["Natureza"].astype(str).str.strip()
                + ")"
            )

            st.subheader("Evolução mensal do total financeiro")
            mensal = (
                df_valid.groupby("Mes", as_index=False)["Saldo_em_Reais"]
                .sum()
                .sort_values("Mes")
            )
            mensal["Mes_label"] = mensal["Mes"].map({1: "Jan", 2: "Fev", 3: "Mar"})
            fig_mensal = px.bar(
                mensal,
                x="Mes_label",
                y="Saldo_em_Reais",
                labels={"Mes_label": "Mês", "Saldo_em_Reais": "Valor (R$)"},
                title="Total mensal de investimentos em banco",
            )
            st.plotly_chart(fig_mensal, use_container_width=True)

            st.subheader(f"Distribuição por instituições financeiras/contas (Mês {mes_recente})")
            por_instituicao = (
                df_valid[df_valid["Mes"] == mes_recente]
                .groupby("instituicao_financeira", as_index=False)["Saldo_em_Reais"]
                .sum()
                .sort_values("Saldo_em_Reais", ascending=False)
            )
            fig_inst = px.bar(
                por_instituicao,
                x="Saldo_em_Reais",
                y="instituicao_financeira",
                orientation="h",
                labels={"Saldo_em_Reais": "Valor (R$)", "instituicao_financeira": "Instituição/Conta"},
                title="Distribuição por instituição financeira/conta",
            )
            st.plotly_chart(fig_inst, use_container_width=True)
