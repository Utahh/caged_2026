#!/usr/bin/env python3
"""Valida admissões, desligamentos, saldo e estoque acumulado por mês (Botucatu)."""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "caged_botucatu_q1_2026.csv"

# Referência pública jan/2026 (prefeitura / imprensa)
REF = {
    (2026, 1): {"adm": 2259, "saldo": 162},
}


def main() -> None:
    rows: list[dict[str, str]] = []
    with CSV.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            rows.append(row)

    by_month: dict[tuple[int, int], dict[str, float]] = {}
    for row in rows:
        y, m = int(row["ano_referencia"]), int(row["mes_referencia"])
        sm = float(row.get("saldomovimentacao", 0) or 0)
        adm = float(row.get("admissao", 0) or 0)
        dem = float(row.get("demissao", 0) or 0)
        key = (y, m)
        if key not in by_month:
            by_month[key] = {"adm": 0.0, "dem": 0.0, "saldo": 0.0}
        by_month[key]["adm"] += adm
        by_month[key]["dem"] += dem
        by_month[key]["saldo"] += sm

    estoque = 0.0
    lines = [f"Arquivo: {CSV}", ""]
    for key in sorted(by_month):
        v = by_month[key]
        estoque += v["saldo"]
        chk = ""
        if key in REF:
            r = REF[key]
            chk = (
                f" | ref adm {r['adm']} ({v['adm'] - r['adm']:+.0f})"
                f" ref saldo {r['saldo']} ({v['saldo'] - r['saldo']:+.0f})"
            )
        lines.append(
            f"{key[0]}-{key[1]:02d}: adm={v['adm']:.0f} dem={v['dem']:.0f} "
            f"saldo={v['saldo']:.0f} adm-dem={v['adm'] - v['dem']:.0f} estoque_acum={estoque:.0f}{chk}"
        )

    out = ROOT / "caged_validacao_serie.txt"
    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
