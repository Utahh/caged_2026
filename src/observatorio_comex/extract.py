"""Camada de extração: mocks para teste imediato e stubs PostgreSQL documentados."""
from __future__ import annotations

from typing import Any

import polars as pl

from observatorio_comex.config import MunicipioAlvo

# ---------------------------------------------------------------------------
# Schemas esperados (colunas mínimas)
# ---------------------------------------------------------------------------
SCHEMA_COMEX = {
    "sh4": pl.Utf8,
    "descricao_sh4": pl.Utf8,
}
SCHEMA_CROSSWALK = {
    "sh4": pl.Utf8,
    "cnae_codigo": pl.Utf8,
    "cnae_prefix": pl.Utf8,
    "motivo_relacao": pl.Utf8,
    "fonte": pl.Utf8,
}
SCHEMA_EMPRESAS = {
    "cnpj": pl.Utf8,
    "razao_social": pl.Utf8,
    "municipio_ibge": pl.Utf8,
    "municipio_nome": pl.Utf8,
    "cnae_fiscal_principal": pl.Utf8,
    "cnaes_secundarios": pl.List(pl.Utf8),
    "situacao_cadastral": pl.Utf8,
}
SCHEMA_HABILITADOS = {
    "cnpj": pl.Utf8,
}


class MockComexCatalogExtractor:
    """MDIC / ranking Comex — substituir por view `comex.dim_sh4` ou API materializada."""

    def extract(self) -> pl.LazyFrame:
        rows = [
            ("0101", "Cavalos, asininos e muares, vivos"),
            ("0201", "Carnes de bovinos, frescas ou refrigeradas"),
            ("7208", "Produtos laminados planos de ferro/aço"),
            ("8471", "Máquinas automáticas processamento dados"),
        ]
        return pl.LazyFrame(
            {"sh4": [r[0] for r in rows], "descricao_sh4": [r[1] for r in rows]},
            schema=SCHEMA_COMEX,
        )


class MockSh4CnaeCrosswalkExtractor:
    """
    Correlação SH4 × CNAE (IBGE/Concla ou tabela interna).

    Produção (PostgreSQL):
    ```sql
    SELECT sh4, cnae_codigo, LEFT(cnae_codigo, 2) AS cnae_prefix_div,
           motivo, fonte
    FROM comex.sh4_cnae_crosswalk
    WHERE vigente = TRUE;
    ```
  """

    def extract(self) -> pl.LazyFrame:
        rows = [
            ("0101", "0141501", "01", "Animais vivos — pecuária", "IBGE/Concla"),
            ("0201", "1011201", "1011", "Abate bovinos", "IBGE/Concla"),
            ("7208", "2423701", "24237", "Laminação a quente", "IBGE/Concla"),
            ("7208", "4683400", "4683", "Comércio atacadista ferro/aço", "aproximação"),
            ("8471", "4751201", "47512", "Comércio varejista informática", "IBGE/Concla"),
        ]
        return pl.LazyFrame(
            {
                "sh4": [r[0] for r in rows],
                "cnae_codigo": [r[1] for r in rows],
                "cnae_prefix": [r[2] for r in rows],
                "motivo_relacao": [r[3] for r in rows],
                "fonte": [r[4] for r in rows],
            },
            schema=SCHEMA_CROSSWALK,
        )


class MockEmpresasMunicipioExtractor:
    """
    **Pushdown crítico:** em produção, o filtro de município deve estar no SQL,
    não após carregar 50M+ linhas.

    ```sql
    SELECT
        e.cnpj,
        e.razao_social,
        est.municipio_ibge,
        est.municipio_nome,
        est.cnae_fiscal_principal,
        est.cnaes_secundarios,  -- array_agg no PG ou JSON parseado
        est.situacao_cadastral
    FROM receita.estabelecimentos est
    JOIN receita.empresas e ON e.cnpj_basico = est.cnpj_basico
    WHERE est.municipio_ibge = :ibge_7
       OR est.municipio_codigo_rfb = ANY(:codigos_rfb)
    ```
    """

    def extract(self, municipio: MunicipioAlvo) -> pl.LazyFrame:
        # Mock nacional pequeno + filtro lazy (simula pushdown)
        all_rows: list[dict[str, Any]] = [
            {
                "cnpj": "12345678000190",
                "razao_social": "Metalúrgica Botucatu Ltda",
                "municipio_ibge": "3507506",
                "municipio_nome": "BOTUCATU",
                "cnae_fiscal_principal": "2423701",
                "cnaes_secundarios": ["4683400", "3314707"],
                "situacao_cadastral": "02",
            },
            {
                "cnpj": "98765432000111",
                "razao_social": "Informática Centro SP",
                "municipio_ibge": "3550308",
                "municipio_nome": "SAO PAULO",
                "cnae_fiscal_principal": "4751201",
                "cnaes_secundarios": [],
                "situacao_cadastral": "02",
            },
            {
                "cnpj": "11223344000155",
                "razao_social": "Comércio Varejista Local",
                "municipio_ibge": "3507506",
                "municipio_nome": "BOTUCATU",
                "cnae_fiscal_principal": "4712100",
                "cnaes_secundarios": ["4683400"],
                "situacao_cadastral": "02",
            },
            {
                "cnpj": "55667788000122",
                "razao_social": "Importadora Radar OK",
                "municipio_ibge": "3507506",
                "municipio_nome": "BOTUCATU",
                "cnae_fiscal_principal": "4683400",
                "cnaes_secundarios": ["2423701"],
                "situacao_cadastral": "02",
            },
        ]
        lf = pl.LazyFrame(all_rows, schema=SCHEMA_EMPRESAS)
        return lf.filter(
            (pl.col("municipio_ibge") == municipio.ibge_7)
            | pl.col("municipio_ibge").is_in(list(municipio.codigos_rfb_estabelecimento))
        )


class MockHabilitadosComexExtractor:
    """
    ```sql
    SELECT cnpj FROM comex.cnpj_habilitado_radar WHERE situacao = 'ATIVO';
    ```
    """

    def extract(self) -> pl.LazyFrame:
        return pl.LazyFrame(
            {"cnpj": ["55667788000122", "99999999000199"]},
            schema=SCHEMA_HABILITADOS,
        )


class PostgresEmpresasMunicipioExtractor:
    """
    Extração via SQLAlchemy/psycopg — implementar quando o warehouse estiver pronto.

    Parâmetros bind: ibge_7, codigos_rfb (lista).
    Retorna LazyFrame via `pl.read_database_uri` ou scan_pyarrow de COPY temporário.
    """

    def __init__(self, connection_uri: str) -> None:
        self._uri = connection_uri

    def extract(self, municipio: MunicipioAlvo) -> pl.LazyFrame:
        raise NotImplementedError(
            "Substitua por pl.read_database_uri(query, self._uri) com o SQL documentado em "
            "MockEmpresasMunicipioExtractor. Garanta índice em municipio_ibge / codigo_rfb."
        )


class PostgresSh4CnaeCrosswalkExtractor:
    def __init__(self, connection_uri: str, table: str = "comex.sh4_cnae_crosswalk") -> None:
        self._uri = connection_uri
        self._table = table

    def extract(self) -> pl.LazyFrame:
        raise NotImplementedError(f"SELECT * FROM {self._table} WHERE vigente IS TRUE")
