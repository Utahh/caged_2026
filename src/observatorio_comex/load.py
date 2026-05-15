"""Camada de carga: Parquet/CSV local ou COPY PostgreSQL."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from observatorio_comex.config import PipelineConfig


class ParquetExporter:
    """Persistência analítica (data lake / staging)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def export(self, df: pl.DataFrame) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(self._path)
        print(f"[load] Parquet: {self._path} ({len(df)} linhas)")


class CsvSemicolonExporter:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def export(self, df: pl.DataFrame) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        df.write_csv(self._path, separator=";")
        print(f"[load] CSV: {self._path} ({len(df)} linhas)")


class PostgresFactExporter:
    """
    ```sql
    CREATE TABLE IF NOT EXISTS comex.fato_sh4_empresa (
        sh4 CHAR(4),
        descricao_sh4 TEXT,
        cnpj CHAR(14),
        razao_social TEXT,
        municipio_ibge CHAR(7),
        cnae_empresa VARCHAR(7),
        tipo_cnae_match VARCHAR(12),
        motivo_relacao TEXT,
        fonte TEXT,
        possui_habilitacao_comex BOOLEAN,
        atualizado_em TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (cnpj, sh4, cnae_empresa, tipo_cnae_match)
    );
  -- COPY ou INSERT via psycopg2.execute_values / polars write_database
    ```
    """

    def __init__(self, connection_uri: str, table: str = "comex.fato_sh4_empresa") -> None:
        self._uri = connection_uri
        self._table = table

    def export(self, df: pl.DataFrame) -> None:
        raise NotImplementedError(
            f"Use df.write_database(table_name='{self._table}', connection_uri=...) "
            "ou COPY FROM STDIN após TRUNCATE da partição municipal."
        )


def exporter_from_config(config: PipelineConfig) -> ParquetExporter | CsvSemicolonExporter:
    if not config.export_path:
        return ParquetExporter("data/processed/fato_sh4_empresas.parquet")
    p = Path(config.export_path)
    if p.suffix.lower() == ".csv":
        return CsvSemicolonExporter(p)
    return ParquetExporter(p)
