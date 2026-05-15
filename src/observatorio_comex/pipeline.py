"""Orquestrador ETL: composição de extractors e transformações."""
from __future__ import annotations

import polars as pl

from observatorio_comex.config import BOTUCATU, PipelineConfig
from observatorio_comex.contracts import (
    ComexCatalogExtractor,
    EmpresasMunicipioExtractor,
    HabilitadosComexExtractor,
    RastreabilidadeExporter,
    Sh4CnaeCrosswalkExtractor,
)
from observatorio_comex.extract import (
    MockComexCatalogExtractor,
    MockEmpresasMunicipioExtractor,
    MockHabilitadosComexExtractor,
    MockSh4CnaeCrosswalkExtractor,
)
from observatorio_comex.load import exporter_from_config
from observatorio_comex.transform import build_fato_rastreabilidade


class ComexCnpjRastreabilidadePipeline:
    """
    Pipeline SH4 (Comex) → CNAE (crosswalk) → CNPJ (Receita), com pushdown municipal.

    Injeção de dependências permite trocar mocks por extractors PostgreSQL
    sem alterar a lógica de transformação.
    """

    def __init__(
        self,
        config: PipelineConfig | None = None,
        *,
        comex_extractor: ComexCatalogExtractor | None = None,
        crosswalk_extractor: Sh4CnaeCrosswalkExtractor | None = None,
        empresas_extractor: EmpresasMunicipioExtractor | None = None,
        habilitados_extractor: HabilitadosComexExtractor | None = None,
        exporter: RastreabilidadeExporter | None = None,
    ) -> None:
        self._config = config or PipelineConfig(municipio=BOTUCATU)
        self._comex = comex_extractor or MockComexCatalogExtractor()
        self._crosswalk = crosswalk_extractor or MockSh4CnaeCrosswalkExtractor()
        self._empresas = empresas_extractor or MockEmpresasMunicipioExtractor()
        self._habilitados = habilitados_extractor or MockHabilitadosComexExtractor()
        self._exporter = exporter or exporter_from_config(self._config)

    def extract(self) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame, pl.LazyFrame]:
        """E — leitura lazy; empresas já filtradas por município na fonte."""
        municipio = self._config.municipio
        return (
            self._comex.extract(),
            self._crosswalk.extract(),
            self._empresas.extract(municipio),
            self._habilitados.extract(),
        )

    def transform(
        self,
        comex: pl.LazyFrame,
        crosswalk: pl.LazyFrame,
        empresas: pl.LazyFrame,
        habilitados: pl.LazyFrame,
    ) -> pl.LazyFrame:
        """T — mantém LazyFrame até o collect final."""
        return build_fato_rastreabilidade(
            empresas, crosswalk, comex, habilitados, self._config
        )

    def load(self, fact: pl.DataFrame) -> None:
        """L — materialização e persistência."""
        self._exporter.export(fact)

    def run(self) -> pl.DataFrame:
        comex, crosswalk, empresas, hab = self.extract()
        fact_lf = self.transform(comex, crosswalk, empresas, hab)
        fact = fact_lf.collect()
        self.load(fact)
        return fact
