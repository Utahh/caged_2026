#!/usr/bin/env python3
"""Valida admissões, desligamentos, saldo e estoque de vínculos por mês (Botucatu)."""
from __future__ import annotations

from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "caged_botucatu_q1_2026.csv"
ANCHOR = ROOT / "data" / "caged_estoque_referencia.csv"

REF = {
    (2026, 1): {"adm": 2259, "saldo": 162},
    (2026, 3): {"estoque": 48309},
}


def _monthly_with_estoque(raw: pl.DataFrame) -> pl.DataFrame:
    monthly = (
        raw.group_by(["ano_referencia", "mes_referencia"])
        .agg(
            [
                pl.col("admissao").cast(pl.Float64).fill_null(0).sum().alias("Admissões"),
                pl.col("demissao").cast(pl.Float64).fill_null(0).sum().alias("Desligamentos"),
                pl.col("saldomovimentacao").cast(pl.Float64).fill_null(0).sum().alias("Saldo"),
            ]
        )
        .sort(["ano_referencia", "mes_referencia"])
        .with_columns((pl.col("ano_referencia") * 100 + pl.col("mes_referencia")).alias("ord_mes"))
    )
    lo, hi = int(monthly["ord_mes"].min()), int(monthly["ord_mes"].max())
    y, m = lo // 100, lo % 100
    y_end, m_end = hi // 100, hi % 100
    keys = []
    while (y, m) <= (y_end, m_end):
        keys.append((y, m, y * 100 + m))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    grid = pl.DataFrame(
        {"ano_referencia": [t[0] for t in keys], "mes_referencia": [t[1] for t in keys], "ord_mes": [t[2] for t in keys]}
    )
    monthly = grid.join(monthly, on=["ano_referencia", "mes_referencia", "ord_mes"], how="left").with_columns(
        [pl.col("Admissões").fill_null(0), pl.col("Desligamentos").fill_null(0), pl.col("Saldo").fill_null(0)]
    )
    monthly = monthly.with_columns(pl.col("Saldo").cum_sum().alias("_ac"))
    base = 0.0
    if ANCHOR.is_file():
        ref = pl.read_csv(ANCHOR, separator=";").tail(1)
        oy, om = int(ref["ano_referencia"][0]), int(ref["mes_referencia"][0])
        est = float(ref["estoque"][0])
        ord_ref = oy * 100 + om
        saldo_ref = float(monthly.filter(pl.col("ord_mes") == ord_ref)["_ac"][0])
        base = est - saldo_ref
    return monthly.with_columns((base + pl.col("_ac")).alias("Estoque")).drop("_ac")


def main() -> None:
    raw = pl.read_csv(CSV, separator=";")
    monthly = _monthly_with_estoque(raw)

    lines = [f"Arquivo: {CSV}", f"Âncora: {ANCHOR if ANCHOR.is_file() else 'não'}", ""]
    for row in monthly.iter_rows(named=True):
        y, m = int(row["ano_referencia"]), int(row["mes_referencia"])
        key = (y, m)
        chk = ""
        if key in REF:
            r = REF[key]
            if "adm" in r:
                chk += f" | ref adm {r['adm']} ({row['Admissões'] - r['adm']:+.0f})"
            if "saldo" in r:
                chk += f" | ref saldo {r['saldo']} ({row['Saldo'] - r['saldo']:+.0f})"
            if "estoque" in r:
                chk += f" | ref estoque {r['estoque']} ({row['Estoque'] - r['estoque']:+.0f})"
        lines.append(
            f"{y}-{m:02d}: adm={row['Admissões']:.0f} dem={row['Desligamentos']:.0f} "
            f"saldo={row['Saldo']:.0f} estoque={row['Estoque']:.0f}{chk}"
        )

    out = ROOT / "caged_validacao_serie.txt"
    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
