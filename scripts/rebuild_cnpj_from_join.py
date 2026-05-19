#!/usr/bin/env python3
"""Reagrega CSVs CNPJ a partir do join já exportado (sem rebaixar ZIPs da RFB)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cnpj_botucatu_etl import export_cnpj_csvs, rebuild_cnpj_aggregates_from_join  # noqa: E402


def main() -> int:
    join_path = ROOT / "cnpj_botucatu_join_empresas.csv"
    resumo_path = ROOT / "cnpj_botucatu_resumo.csv"
    if not join_path.is_file():
        print(f"Arquivo não encontrado: {join_path}", file=sys.stderr)
        return 1

    print(f"Lendo {join_path} ...")
    join_df = pd.read_csv(join_path, sep=";", dtype=str, low_memory=False)
    # booleans exportados como 0/1
    for col in ("mei_simples_vigente", "mei_ativo", "mei_inativo_cnpj"):
        if col in join_df.columns:
            join_df[col] = join_df[col].astype(str).str.strip().isin(["1", "True", "true", "S"])

    meta: dict = {}
    if resumo_path.is_file():
        r0 = pd.read_csv(resumo_path, sep=";", dtype=str).iloc[0].to_dict()
        meta = {
            "ref_data_extracao": r0.get("ref_data_extracao", ""),
            "fonte_url": r0.get("fonte_url", ""),
            "municipio_ibge": r0.get("municipio_ibge", ""),
            "municipio_nome": r0.get("municipio_nome", "Botucatu"),
            "total_estabelecimentos": r0.get("total_estabelecimentos", ""),
        }
        mf = ROOT / "cnpj_botucatu_municipio_fonte.csv"
        if mf.is_file():
            meta["municipio_fonte_path"] = str(mf)

    print("Reagregando (MEI / porte / setor) ...")
    dfs = rebuild_cnpj_aggregates_from_join(join_df, resumo_meta=meta)
    mei_ativos = int(dfs["resumo"]["mei_ativos"].iloc[0])
    mei_vigente = int(dfs["resumo"]["mei_opcao_sem_exclusao"].iloc[0])
    print(f"mei_ativos={mei_ativos}  mei_opcao_sem_exclusao={mei_vigente}")

    export_cnpj_csvs(dfs, out_dir=ROOT)
    print("OK — CSVs atualizados na raiz do projeto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
