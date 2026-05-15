"""Integração com o pipeline municipal: gera fato SH4×CNPJ e exporta CSV."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from observatorio_comex.config import BOTUCATU, PipelineConfig
from observatorio_comex.extract import MockEmpresasMunicipioExtractor
from observatorio_comex.extract_csv import (
    CsvComexSh4CatalogExtractor,
    CsvCnpjJoinEmpresasExtractor,
    CsvSh4CnaeCrosswalkExtractor,
    EmptyHabilitadosExtractor,
)
from observatorio_comex.load import CsvSemicolonExporter
from observatorio_comex.pipeline import ComexCnpjRastreabilidadePipeline


def run_rastreabilidade_botucatu(root: Path | None = None) -> pl.DataFrame:
    base = root or Path(__file__).resolve().parents[2]
    out_csv = base / "data" / "processed" / "fato_sh4_empresas_botucatu.csv"
    config = PipelineConfig(municipio=BOTUCATU, export_path=str(out_csv))
    join_path = base / "cnpj_botucatu_join_empresas.csv"
    empresas_extractor = (
        CsvCnpjJoinEmpresasExtractor(join_path)
        if join_path.is_file()
        else MockEmpresasMunicipioExtractor()
    )
    pipeline = ComexCnpjRastreabilidadePipeline(
        config=config,
        comex_extractor=CsvComexSh4CatalogExtractor(base),
        crosswalk_extractor=CsvSh4CnaeCrosswalkExtractor(base / "data" / "comex_sh4_cnae_aproximacao.csv"),
        empresas_extractor=empresas_extractor,
        habilitados_extractor=EmptyHabilitadosExtractor(),
        exporter=CsvSemicolonExporter(out_csv),
    )
    return pipeline.run()
