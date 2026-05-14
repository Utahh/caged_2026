"""Compara totais do CSV local com referência pública (prefeitura/MTE) — jan/2026 Botucatu."""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "caged_botucatu_q1_2026.csv"

# Divulgação citada pela Prefeitura de Botucatu (CAGED jan/2026).
REF_SALDO = 162
REF_ADMISSOES = 2259


def main() -> None:
    rows_2026_01: list[dict[str, str]] = []
    with CSV.open(encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f, delimiter=";")
        for row in r:
            if row.get("ano_referencia") == "2026" and row.get("mes_referencia") == "1":
                rows_2026_01.append(row)

    def fnum(x: str) -> float:
        try:
            return float((x or "0").replace(",", "."))
        except ValueError:
            return 0.0

    adm_err = dem_err = liq = 0.0
    adm_c = dem_c = 0.0
    for row in rows_2026_01:
        sm = fnum(row.get("saldomovimentacao", "0"))
        liq += sm
        if sm > 0:
            adm_err += sm
        elif sm < 0:
            dem_err += -sm
        adm_c += fnum(row.get("admissao", "0"))
        dem_c += fnum(row.get("demissao", "0"))

    lines = [
        f"Arquivo: {CSV}",
        f"Linhas (2026-01): {len(rows_2026_01)}",
        "AVISO: em CSV agregado por CNAE, NAO use sum(max(saldo,0)) por linha — subconta.",
        f"  (exemplo errado) Adm por max(saldo,0)/linha: {adm_err:.0f}; Dem: {dem_err:.0f}",
        f"Saldo liquido (sum saldo): {liq:.0f}",
        f"Admissoes (coluna CSV admissao, correto p/ este export): {adm_c:.0f}",
        f"Desligamentos (coluna CSV demissao): {dem_c:.0f}",
        "",
        "Referencia publica (Prefeitura Botucatu, CAGED jan/2026):",
        f"  Admissoes: {REF_ADMISSOES}",
        f"  Saldo: {REF_SALDO}",
        f"  Desligamentos implicitos (adm - saldo): {REF_ADMISSOES - REF_SALDO}",
        "",
        "Diferencas (colunas CSV vs referencia divulgacao):",
        f"  Admissoes: {adm_c - REF_ADMISSOES:+.0f}",
        f"  Saldo: {liq - REF_SALDO:+.0f}",
        f"  Desligamentos: {dem_c - (REF_ADMISSOES - REF_SALDO):+.0f}",
    ]
    out = ROOT / "caged_validacao_jan2026.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
