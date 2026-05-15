"""Extractors que leem artefatos CSV já gerados pelo repositório (Botucatu)."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from observatorio_comex.config import MunicipioAlvo
from observatorio_comex.extract import SCHEMA_COMEX, SCHEMA_CROSSWALK, SCHEMA_EMPRESAS, SCHEMA_HABILITADOS


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


class CsvSh4CnaeCrosswalkExtractor:
    """`data/comex_sh4_cnae_aproximacao.csv` (sh4; cnae_prefix; nota)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _root() / "data" / "comex_sh4_cnae_aproximacao.csv"

    def extract(self) -> pl.LazyFrame:
        lf = pl.scan_csv(self._path, separator=";", truncate_ragged_lines=True)
        return lf.select(
            pl.col("sh4").cast(pl.Utf8).str.zfill(4),
            pl.col("cnae_prefix").cast(pl.Utf8).str.strip_chars().alias("cnae_codigo"),
            pl.col("cnae_prefix").cast(pl.Utf8).str.strip_chars().alias("cnae_prefix"),
            pl.col("nota").cast(pl.Utf8).fill_null("").alias("motivo_relacao"),
            pl.lit("aproximacao_csv").alias("fonte"),
        )


class CsvComexSh4CatalogExtractor:
    """União dos rankings Comex export/import (descrição SH4)."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or _root()

    def extract(self) -> pl.LazyFrame:
        paths = [
            self._root / "comex_botucatu_top_sh4_export.csv",
            self._root / "comex_botucatu_top_sh4_import.csv",
        ]
        frames: list[pl.LazyFrame] = []
        for p in paths:
            if not p.is_file():
                continue
            cols = pl.read_csv(p, separator=";", n_rows=0).columns
            if "sh4" not in cols:
                continue
            lf = pl.scan_csv(p, separator=";", truncate_ragged_lines=True)
            desc_col = "descricao" if "descricao" in cols else "descricao_sh4"
            frames.append(
                lf.select(
                    pl.col("sh4").cast(pl.Utf8).str.zfill(4),
                    pl.col(desc_col).cast(pl.Utf8).alias("descricao_sh4"),
                )
            )
        if not frames:
            return pl.LazyFrame(schema=SCHEMA_COMEX)
        out = pl.concat(frames, how="vertical_relaxed").unique("sh4")
        return out


class CsvCnpjJoinEmpresasExtractor:
    """
    `cnpj_botucatu_join_empresas.csv` — layout do ETL CNPJ do projeto.
    Secundários: coluna única ou lista separada por vírgula (se existir no futuro).
    """

    def __init__(self, path: Path | None = None, municipio_ibge: str = "3507506") -> None:
        self._path = path or _root() / "cnpj_botucatu_join_empresas.csv"
        self._ibge = municipio_ibge

    def extract(self, municipio: MunicipioAlvo) -> pl.LazyFrame:
        if not self._path.is_file():
            return pl.LazyFrame(schema=SCHEMA_EMPRESAS)
        lf = pl.scan_csv(self._path, separator=";", truncate_ragged_lines=True, infer_schema_length=2000)
        schema = list(pl.read_csv(self._path, separator=";", n_rows=0).columns)
        cnae_col = "cnae_fiscal_principal" if "cnae_fiscal_principal" in schema else "cnae_subclasse"
        sec_col = "cnaes_secundarios" if "cnaes_secundarios" in schema else None

        exprs = [
            pl.col("cnpj").cast(pl.Utf8).str.replace_all(r"\D", "").alias("cnpj"),
            pl.col("razao_social").cast(pl.Utf8).fill_null("").alias("razao_social")
            if "razao_social" in schema
            else pl.lit("").alias("razao_social"),
            pl.lit(municipio.ibge_7).alias("municipio_ibge"),
            pl.lit(municipio.nome).alias("municipio_nome"),
            pl.col(cnae_col).cast(pl.Utf8).str.replace_all(r"\D", "").alias("cnae_fiscal_principal"),
            pl.lit("02").alias("situacao_cadastral"),
        ]
        if sec_col:
            exprs.append(
                pl.when(pl.col(sec_col).is_null() | (pl.col(sec_col) == ""))
                .then(pl.lit([]))
                .otherwise(pl.col(sec_col).str.split(","))
                .alias("cnaes_secundarios")
            )
        else:
            exprs.append(pl.lit([]).alias("cnaes_secundarios"))

        return lf.select(exprs).filter(pl.col("cnpj").str.len_chars() >= 8)


class EmptyHabilitadosExtractor:
    """Placeholder até ingestão da base Radar/Siscomex."""

    def extract(self) -> pl.LazyFrame:
        return pl.LazyFrame(schema=SCHEMA_HABILITADOS)
