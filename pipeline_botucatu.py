import json
import os
import shutil
import time
import urllib.error
import urllib.request
import unicodedata
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import py7zr
import requests


BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "tmp_pipeline_botucatu"
# Sobrescreva para retomar após interrupção (ex.: PIPELINE_CAGED_START_YEAR=2025 PIPELINE_CAGED_START_MONTH=4)
START_YEAR = int(os.environ.get("PIPELINE_CAGED_START_YEAR", "2024"))
START_MONTH = int(os.environ.get("PIPELINE_CAGED_START_MONTH", "1"))
CURRENT_DATE = date.today()
YEAR = str(CURRENT_DATE.year)
FINANCIAL_YEARS = ["2025", "2026"]
BOTUCATU_MUNICIPIO_CAGED = 350750
BOTUCATU_ID_ENTE_SICONFI = 3507506
MUNICIPIOS_COMPARATIVO_CAGED = {
    350750: "Botucatu",
    354520: "Salto",
    352530: "Jaú",
    355170: "Sertãozinho",
    355400: "Tatuí",
}
POUPANCA_CODIGOS = {"111310100", "111310200"}
MAPA_BANCOS = {
    "111110100": "Caixa da Prefeitura",
    "111110200": "Banco do Brasil",
    "111110603": "Caixa Econômica Federal",
    "111110604": "Santander / Outros",
    "111111900": "Fundos de Investimento",
    "111115000": "Aplicações em Renda Fixa",
    "111310100": "Poupança Municipal",
    "111310200": "Poupança Vinculada",
}


def log(msg: str) -> None:
    print(f"[PIPELINE] {msg}", flush=True)


def retry_exec(func, attempts: int = 3, sleep_seconds: int = 3, context: str = ""):
    last_error = None
    for i in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            log(f"Falha ({i}/{attempts}) em {context}: {exc}")
            if i < attempts:
                time.sleep(sleep_seconds)
    raise RuntimeError(f"Erro após {attempts} tentativas em {context}") from last_error


def detect_delimiter(file_path: Path) -> str:
    with file_path.open("r", encoding="utf-8", errors="ignore") as f:
        sample = "".join([f.readline() for _ in range(5)])
    candidates = [";", ",", "\t", "|"]
    counts = {d: sample.count(d) for d in candidates}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ";"


def normalize_col_map(columns: Iterable[str]) -> Dict[str, str]:
    mapped = {}
    for c in columns:
        raw = str(c).strip().lower()
        ascii_key = (
            unicodedata.normalize("NFKD", raw)
            .encode("ascii", "ignore")
            .decode("ascii")
            .strip()
            .lower()
        )
        mapped[ascii_key] = c
    return mapped


def first_present(col_map: Dict[str, str], candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in col_map:
            return col_map[c]
    return None


def normalize_subclasse_code(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits.zfill(7) if digits else ""


def build_caged_periods(start_year: int, start_month: int) -> List[Tuple[int, int]]:
    periods: List[Tuple[int, int]] = []
    y, m = start_year, start_month
    while (y < CURRENT_DATE.year) or (y == CURRENT_DATE.year and m <= CURRENT_DATE.month):
        periods.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return periods


def download_file(url: str, output_path: Path) -> None:
    def _do_download():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response, output_path.open("wb") as out:
            shutil.copyfileobj(response, out)

    retry_exec(_do_download, attempts=3, sleep_seconds=5, context=f"download FTP {url}")


def extract_7z(archive_path: Path, output_dir: Path) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(archive_path, mode="r") as z:
        names = z.getnames()
        z.extractall(path=output_dir)
    return [output_dir / n for n in names]


def resolve_caged_columns(raw_file: Path, delimiter: str) -> Tuple[Dict[str, str], List[str], Dict[str, object]]:
    """
    Resolve colunas obrigatórias + período da movimentação (competência do evento).

    O Novo CAGED usa a **competência da movimentação** (mês a que o adm/deslig se refere),
    não o mês da pasta FTP. FOR/EXC retroativos devem ser agregados nesse ano/mês.
    """
    header_df = pd.read_csv(raw_file, sep=delimiter, nrows=0, encoding="utf-8", low_memory=False)
    col_map = normalize_col_map(header_df.columns)

    col_municipio = first_present(col_map, ["municipio", "id_municipio", "codmunicipio"])
    col_secao = first_present(col_map, ["secao"])
    col_subclasse = first_present(col_map, ["subclasse", "subclassecnae20", "subclasse_cnae"])
    col_saldo = first_present(col_map, ["saldomovimentacao", "saldo_movimentacao", "saldo"])

    mandatory = {"municipio": col_municipio, "secao": col_secao, "subclasse": col_subclasse, "saldo": col_saldo}
    missing = [k for k, v in mandatory.items() if not v]
    if missing:
        raise ValueError(
            f"Arquivo CAGED sem colunas obrigatórias ({', '.join(missing)}). "
            f"Colunas detectadas: {list(header_df.columns)}"
        )

    col_competencia = first_present(col_map, ["competencia", "competncia"])
    col_ano_mov = first_present(
        col_map,
        [
            "ano_competencia_movimento",
            "ano_competencia",
            "ano_movimento",
            "ano_movimentacao",
        ],
    )
    col_mes_mov = first_present(
        col_map,
        [
            "mes_competencia_movimento",
            "mes_competencia",
            "mes_movimento",
            "mes_movimentacao",
        ],
    )

    period_spec: Dict[str, object]
    if col_competencia:
        period_spec = {"mode": "competencia", "col": col_competencia}
    elif col_ano_mov and col_mes_mov:
        period_spec = {"mode": "ano_mes", "ano": col_ano_mov, "mes": col_mes_mov}
    else:
        period_spec = {"mode": "folder"}

    usecols = [col_municipio, col_secao, col_subclasse, col_saldo]
    if period_spec["mode"] == "competencia":
        usecols.append(str(period_spec["col"]))
    elif period_spec["mode"] == "ano_mes":
        usecols.append(str(period_spec["ano"]))
        usecols.append(str(period_spec["mes"]))
    usecols = list(dict.fromkeys(usecols))

    base_map = {
        "municipio": col_municipio,
        "secao": col_secao,
        "subclasse": col_subclasse,
        "saldo": col_saldo,
    }
    return base_map, usecols, period_spec


def parse_competencia_aaaamm(series: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """Interpreta coluna competência como AAAAMM (número ou texto)."""
    num = pd.to_numeric(series, errors="coerce")
    ano = (num // 100).astype(float)
    mes = (num % 100).astype(float)
    ok = (mes >= 1) & (mes <= 12) & (ano >= 1980) & (ano <= 2100)
    fix = ~ok & series.notna()
    if fix.any():
        digits = series[fix].astype(str).str.replace(r"\D", "", regex=True).str.slice(0, 6)
        sn = pd.to_numeric(digits, errors="coerce")
        ano.loc[fix] = (sn // 100).astype(float)
        mes.loc[fix] = (sn % 100).astype(float)
    return ano, mes


def assign_movimento_period(
    df: pd.DataFrame,
    period_spec: Dict[str, object],
    folder_year: int,
    folder_month: int,
) -> None:
    """Preenche ano_referencia e mes_referencia in-place (competência do evento)."""
    mode = period_spec.get("mode", "folder")
    if mode == "competencia":
        col = str(period_spec["col"])
        ano, mes = parse_competencia_aaaamm(df[col])
        df["ano_referencia"] = ano
        df["mes_referencia"] = mes
    elif mode == "ano_mes":
        df["ano_referencia"] = pd.to_numeric(df[str(period_spec["ano"])], errors="coerce")
        df["mes_referencia"] = pd.to_numeric(df[str(period_spec["mes"])], errors="coerce")
    else:
        df["ano_referencia"] = float(folder_year)
        df["mes_referencia"] = float(folder_month)

    bad = (
        df["mes_referencia"].isna()
        | (df["mes_referencia"] < 1)
        | (df["mes_referencia"] > 12)
        | df["ano_referencia"].isna()
    )
    if bad.any():
        df.loc[bad, "ano_referencia"] = float(folder_year)
        df.loc[bad, "mes_referencia"] = float(folder_month)

    df["ano_referencia"] = pd.to_numeric(df["ano_referencia"], errors="coerce").fillna(folder_year).astype(int)
    df["mes_referencia"] = pd.to_numeric(df["mes_referencia"], errors="coerce").fillna(folder_month).astype(int)


def _cleanup_caged_extract(archive_path: Path, raw_file: Path | None, extract_dir: Path) -> None:
    try:
        if archive_path.exists():
            archive_path.unlink()
        if raw_file is not None and raw_file.exists():
            raw_file.unlink()
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
    except Exception as cleanup_exc:
        log(f"Aviso de limpeza CAGED ({archive_path.name}): {cleanup_exc}")


def process_caged_month(year: int, month: int) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Consolida MOV + FOR + EXC da **mesma** competência (mesma pasta FTP / mesmo snapshot).

    Regra para não inflar totais:
    - Baixar todos os .7z disponíveis da pasta `.../NOVO CAGED/AAAA/AAAAMM/` na mesma execução.
    - Cada linha é contada **uma vez**; o saldo oficial é a soma de `saldomovimentacao` sobre a união
      das linhas (MOV ∪ FOR ∪ EXC). O MTPE organiza os arquivos para que não se some o mesmo
      movimento duas vezes entre arquivos da **mesma** publicação.
    - Não misturar MOV de um mês de download com FOR de outro: isso sim geraria duplicidade.

    Competência do **evento** (adm/deslig): usa coluna `competencia` (AAAAMM) ou ano/mês de movimento do layout,
    quando existirem. Assim, declarações FOR que retificam meses anteriores entram nos totais daquele ano/mês,
    alinhado à divulgação do Novo CAGED. Se o layout não trouxer essas colunas, cai no mês da pasta FTP.
    """
    month_txt = f"{month:02d}"
    year_txt = str(year)
    base_url = f"ftp://ftp.mtps.gov.br/pdet/microdados/NOVO%20CAGED/{year_txt}/{year_txt}{month_txt}/"

    # (prefixo arquivo, obrigatório)
    fontes: List[Tuple[str, bool]] = [
        ("CAGEDMOV", True),
        ("CAGEDFOR", False),
        ("CAGEDEXC", False),
    ]

    frames: List[pd.DataFrame] = []
    frames_comp: List[pd.DataFrame] = []
    col_map: Optional[Dict[str, str]] = None
    file_delimiter: Optional[str] = None
    usecols: Optional[List[str]] = None
    period_spec: Optional[Dict[str, object]] = None
    logged_period = False

    log(
        f"Iniciando CAGED competência {year_txt}-{month_txt}: "
        "MOV (obrigatório) + FOR/EXC quando existirem, sempre do mesmo diretório FTP."
    )

    for prefix, obrigatorio in fontes:
        fname = f"{prefix}{year_txt}{month_txt}.7z"
        archive_path = WORK_DIR / fname
        extract_dir = WORK_DIR / f"extract_{year_txt}_{month_txt}_{prefix}"
        url = base_url + fname
        try:
            log(f"Baixando {fname} …")
            download_file(url, archive_path)
        except (RuntimeError, urllib.error.URLError, OSError) as exc:
            if obrigatorio:
                raise
            log(f"Arquivo opcional {fname} não disponível (pulando): {exc}")
            continue

        log(f"Download concluído: {archive_path.name}")
        extracted_files = extract_7z(archive_path, extract_dir)
        txt_files = [p for p in extracted_files if p.suffix.lower() in (".txt", ".csv")]
        if not txt_files:
            _cleanup_caged_extract(archive_path, None, extract_dir)
            if obrigatorio:
                raise RuntimeError(f"Nenhum TXT/CSV extraído de {fname}")
            log(f"Nenhum TXT/CSV em {fname}; pulando.")
            continue

        raw_file = txt_files[0]
        delim = detect_delimiter(raw_file)
        if col_map is None:
            file_delimiter = delim
            col_map, usecols, period_spec = resolve_caged_columns(raw_file, file_delimiter)
            ps = period_spec.get("mode", "folder")
            if ps == "folder":
                log(
                    f"Aviso layout CAGED: coluna de competência da movimentação não encontrada; "
                    f"usando mês da pasta FTP ({year_txt}-{month_txt}) para adm/des/saldo. "
                    "Confira o Layout Novo CAGED Movimentação se os totais divergirem do portal."
                )
            else:
                log(f"Período da movimentação: modo '{ps}' (colunas conforme layout oficial).")
            log(
                f"Layout CAGED ({prefix}): delimitador '{file_delimiter}', "
                f"processando em chunks de 100.000 linhas — arquivo {raw_file.name}."
            )
            logged_period = True
        else:
            if delim != file_delimiter:
                log(f"Aviso: delimitador diferente em {fname} ({delim} vs {file_delimiter}); usando o de {fname}.")
                file_delimiter = delim
            col_map2, usecols2, period_spec2 = resolve_caged_columns(raw_file, file_delimiter)
            if usecols2 != usecols or col_map2 != col_map:
                log(
                    f"Aviso: colunas divergentes em {fname} em relação ao MOV; "
                    "revalidando colunas a partir deste arquivo."
                )
                col_map, usecols, period_spec = col_map2, usecols2, period_spec2
                if not logged_period:
                    ps = period_spec.get("mode", "folder")
                    if ps == "folder":
                        log(
                            f"Aviso layout CAGED ({fname}): sem competência da movimentação; "
                            f"fallback pasta FTP {year_txt}-{month_txt}."
                        )
                    logged_period = True

        assert col_map is not None and file_delimiter is not None and usecols is not None and period_spec is not None

        for idx, chunk in enumerate(
            pd.read_csv(
                raw_file,
                sep=file_delimiter,
                encoding="utf-8",
                chunksize=100_000,
                usecols=usecols,
                low_memory=False,
            ),
            start=1,
        ):
            chunk[col_map["municipio"]] = pd.to_numeric(chunk[col_map["municipio"]], errors="coerce").fillna(0).astype(
                int
            )
            filtered = chunk[chunk[col_map["municipio"]] == BOTUCATU_MUNICIPIO_CAGED].copy()
            if filtered.empty:
                if idx % 10 == 0:
                    log(f"{prefix} {year_txt}-{month_txt}: chunk {idx} (sem linhas de Botucatu).")
                continue

            assign_movimento_period(filtered, period_spec, year, month)
            filtered["saldomovimentacao"] = pd.to_numeric(filtered[col_map["saldo"]], errors="coerce").fillna(
                0
            ).astype(int)
            filtered["admissao"] = (filtered["saldomovimentacao"] == 1).astype(int)
            filtered["demissao"] = (filtered["saldomovimentacao"] == -1).astype(int)

            month_df = pd.DataFrame(
                {
                    "ano_referencia": filtered["ano_referencia"],
                    "mes_referencia": filtered["mes_referencia"],
                    "secao": filtered[col_map["secao"]].astype(str).str.strip(),
                    "subclasse": filtered[col_map["subclasse"]].astype(str).str.strip(),
                    "saldomovimentacao": filtered["saldomovimentacao"],
                    "admissao": filtered["admissao"],
                    "demissao": filtered["demissao"],
                }
            )
            frames.append(month_df)

            filtered_comp = chunk[chunk[col_map["municipio"]].isin(MUNICIPIOS_COMPARATIVO_CAGED.keys())].copy()
            if not filtered_comp.empty:
                assign_movimento_period(filtered_comp, period_spec, year, month)
                filtered_comp["saldomovimentacao"] = (
                    pd.to_numeric(filtered_comp[col_map["saldo"]], errors="coerce").fillna(0).astype(int)
                )
                comp_df = pd.DataFrame(
                    {
                        "ano_referencia": filtered_comp["ano_referencia"],
                        "mes_referencia": filtered_comp["mes_referencia"],
                        "municipio_codigo": filtered_comp[col_map["municipio"]].astype(int),
                        "Saldo": filtered_comp["saldomovimentacao"],
                    }
                )
                frames_comp.append(comp_df)

            if idx % 10 == 0:
                log(
                    f"{prefix} {year_txt}-{month_txt}: chunk {idx}, "
                    f"linhas Botucatu acumuladas: {sum(len(f) for f in frames)}"
                )

        _cleanup_caged_extract(archive_path, raw_file, extract_dir)

    if not frames:
        return (
            pd.DataFrame(
                columns=[
                    "ano_referencia",
                    "mes_referencia",
                    "secao",
                    "subclasse",
                    "saldomovimentacao",
                    "admissao",
                    "demissao",
                ]
            ),
            pd.DataFrame(columns=["ano_referencia", "mes_referencia", "municipio_codigo", "Saldo"]),
        )

    month_all = pd.concat(frames, ignore_index=True)
    month_comp = (
        pd.concat(frames_comp, ignore_index=True)
        if frames_comp
        else pd.DataFrame(columns=["ano_referencia", "mes_referencia", "municipio_codigo", "Saldo"])
    )
    return month_all, month_comp


def run_caged_etl() -> Tuple[pd.DataFrame, pd.DataFrame]:
    log("ETL CAGED iniciado.")
    periods = build_caged_periods(START_YEAR, START_MONTH)
    log(
        f"CAGED será processado de {START_YEAR}-{START_MONTH:02d} até {CURRENT_DATE.year}-{CURRENT_DATE.month:02d}. "
        "Cada mês de referência nos relatórios usa a competência da movimentação do layout; "
        "reprocessar todo o intervalo após novas publicações FTP mantém séries alinhadas ao Novo CAGED."
    )
    monthly_results = []
    comp_results = []
    for year, month in periods:
        try:
            month_df, month_comp = process_caged_month(year, month)
            log(f"CAGED mês {year}-{month:02d} finalizado. Linhas de Botucatu: {len(month_df)}")
            monthly_results.append(month_df)
            if not month_comp.empty:
                comp_results.append(month_comp)
        except Exception as exc:
            log(f"Erro ao processar mês {year}-{month:02d}: {exc}")

    if not monthly_results:
        raise RuntimeError("Nenhum mês CAGED foi processado com sucesso.")

    caged = pd.concat(monthly_results, ignore_index=True)
    if caged.empty:
        log("CAGED consolidado vazio para Botucatu.")
        return caged, pd.DataFrame(columns=["ano_referencia", "mes_referencia", "Municipio", "Saldo"])

    caged = (
        caged.groupby(["ano_referencia", "mes_referencia", "secao", "subclasse"], as_index=False)[
            ["saldomovimentacao", "admissao", "demissao"]
        ]
        .sum()
        .sort_values(["ano_referencia", "mes_referencia", "secao", "subclasse"])
    )
    caged["subclasse"] = caged["subclasse"].map(normalize_subclasse_code)
    caged["estoque_anual_2026"] = caged.groupby(["secao", "subclasse"])["saldomovimentacao"].transform("sum")
    comp = pd.DataFrame(columns=["ano_referencia", "mes_referencia", "Municipio", "Saldo"])
    if comp_results:
        comp = pd.concat(comp_results, ignore_index=True)
        comp = (
            comp.groupby(["ano_referencia", "mes_referencia", "municipio_codigo"], as_index=False)["Saldo"]
            .sum()
            .sort_values(["ano_referencia", "mes_referencia", "municipio_codigo"])
        )
        comp["Municipio"] = comp["municipio_codigo"].map(MUNICIPIOS_COMPARATIVO_CAGED).fillna("Outros")
        comp = comp[["ano_referencia", "mes_referencia", "Municipio", "Saldo"]]
    return caged, comp


def fetch_siconfi_json() -> Dict:
    raise NotImplementedError("Use fetch_siconfi_month_json for required parameterized calls.")


def fetch_siconfi_month_json(year: str, month: int) -> Dict:
    base_url = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/msc_patrimonial"
    params = {
        "id_ente": BOTUCATU_ID_ENTE_SICONFI,
        "an_referencia": year,
        "me_referencia": month,
        "co_tipo_matriz": "MSCC",
        "classe_conta": 1,
        "id_tv": "ending_balance",
        "limit": 5000,
    }

    session = requests.Session()
    session.headers.update({"User-Agent": "pipeline-botucatu/1.0"})

    def _do_request():
        response = session.get(base_url, params=params, timeout=60)
        response.raise_for_status()
        return response.json()

    return retry_exec(
        _do_request, attempts=3, sleep_seconds=5, context=f"requisição API Siconfi {year}-{month:02d}"
    )


def fetch_cnae_subclasses_reference() -> pd.DataFrame:
    url = "https://servicodados.ibge.gov.br/api/v2/cnae/subclasses"

    def _do_request():
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        return r.json()

    payload = retry_exec(_do_request, attempts=3, sleep_seconds=3, context="API IBGE CNAE")
    if not isinstance(payload, list):
        return pd.DataFrame()

    rows = []
    for item in payload:
        classe = item.get("classe", {}) or {}
        grupo = classe.get("grupo", {}) or {}
        divisao = grupo.get("divisao", {}) or {}
        secao = divisao.get("secao", {}) or {}
        rows.append(
            {
                "subclasse": normalize_subclasse_code(item.get("id", "")),
                "subclasse_descricao": str(item.get("descricao", "")).strip(),
                "classe_cnae": str(classe.get("id", "")).strip(),
                "classe_descricao": str(classe.get("descricao", "")).strip(),
                "grupo_cnae": str(grupo.get("id", "")).strip(),
                "grupo_descricao": str(grupo.get("descricao", "")).strip(),
                "divisao_cnae": str(divisao.get("id", "")).strip(),
                "divisao_descricao": str(divisao.get("descricao", "")).strip(),
                "secao_cnae": str(secao.get("id", "")).strip(),
                "secao_descricao": str(secao.get("descricao", "")).strip(),
            }
        )
    ref = pd.DataFrame(rows).drop_duplicates(subset=["subclasse"])
    return ref


def enrich_caged_with_cnae_descriptions(caged: pd.DataFrame) -> pd.DataFrame:
    if caged.empty:
        return caged
    try:
        ref = fetch_cnae_subclasses_reference()
        if ref.empty:
            log("Referência IBGE CNAE vazia; seguindo sem descrições.")
            return caged
        out = caged.merge(ref, on="subclasse", how="left")
        out["secao"] = out["secao"].astype(str).str.strip()
        out["secao_descricao"] = out["secao_descricao"].fillna("")
        # Usa seção do CAGED como fallback quando referência não casar
        out["secao_cnae"] = out["secao_cnae"].fillna(out["secao"])
        return out
    except Exception as exc:
        log(f"Falha ao enriquecer descrições CNAE: {exc}")
        return caged


def run_siconfi_etl() -> pd.DataFrame:
    log("ETL Siconfi iniciado.")
    items: List[Dict] = []
    for year in FINANCIAL_YEARS:
        for month in range(1, 13):
            payload = fetch_siconfi_month_json(year, month)
            month_items = payload.get("items", []) if isinstance(payload, dict) else []
            log(f"Siconfi {year}-{month:02d}: {len(month_items)} registros recebidos.")
            items.extend(month_items)

    if not items:
        log("Siconfi retornou JSON vazio ou sem itens.")
        return pd.DataFrame(columns=["Mês", "Código Contábil", "Natureza", "Saldo em Reais"])

    df = pd.DataFrame(items)
    col_map = normalize_col_map(df.columns)

    col_conta = first_present(col_map, ["conta_contabil", "co_conta_contabil", "codigo_contabil"])
    col_mes = first_present(col_map, ["mes_referencia", "me_referencia", "mes", "no_mes"])
    col_ano = first_present(col_map, ["an_referencia", "ano_referencia", "ano"])
    col_natureza = first_present(
        col_map, ["natureza", "natureza_conta", "no_natureza_conta", "ds_natureza_conta"]
    )
    col_saldo = first_present(col_map, ["vl_saldo_final", "saldo", "saldo_em_reais", "valor"])

    if not all([col_conta, col_mes, col_natureza, col_saldo]):
        raise ValueError(f"Estrutura inesperada do Siconfi. Colunas recebidas: {list(df.columns)}")

    df[col_conta] = df[col_conta].astype(str).str.strip()
    conta_norm = df[col_conta].str.replace(".", "", regex=False).str.replace("-", "", regex=False)
    df = df[conta_norm.str.startswith("111")].copy()
    if "tipo_valor" in df.columns:
        df = df[df["tipo_valor"].astype(str).str.lower() == "ending_balance"]

    out = pd.DataFrame(
        {
            "Ano": pd.to_numeric(df[col_ano], errors="coerce") if col_ano else int(YEAR),
            "Mês": pd.to_numeric(df[col_mes], errors="coerce"),
            "Código Contábil": df[col_conta],
            "Natureza": df[col_natureza].astype(str).str.strip(),
            "Saldo em Reais": pd.to_numeric(df[col_saldo], errors="coerce").fillna(0),
        }
    ).dropna(subset=["Mês", "Ano"])

    out["Ano"] = out["Ano"].astype(int)
    out["Mês"] = out["Mês"].astype(int)
    out["Código Contábil"] = out["Código Contábil"].astype(str).str.strip()
    out = (
        out.groupby(["Ano", "Mês", "Código Contábil", "Natureza"], as_index=False)["Saldo em Reais"]
        .sum()
        .sort_values(["Ano", "Mês", "Código Contábil", "Natureza"])
    )
    return out


def build_estban_like_dataset(siconfi: pd.DataFrame) -> pd.DataFrame:
    if siconfi.empty:
        return pd.DataFrame(columns=["Ano", "Mes", "instituicao", "valor_poupanca", "data_ref"])

    fin = siconfi.copy()
    fin["codigo_norm"] = (
        fin["Código Contábil"].astype(str).str.replace(".", "", regex=False).str.replace("-", "", regex=False)
    )
    fin = fin[fin["codigo_norm"].isin(POUPANCA_CODIGOS)].copy()
    if fin.empty:
        return pd.DataFrame(columns=["Ano", "Mes", "instituicao", "valor_poupanca", "data_ref"])

    fin["instituicao"] = fin["codigo_norm"].map(MAPA_BANCOS).fillna("Outros")
    fin["Mes"] = pd.to_numeric(fin["Mês"], errors="coerce").fillna(0).astype(int)
    fin["Ano"] = pd.to_numeric(fin["Ano"], errors="coerce").fillna(0).astype(int)

    estban = (
        fin.groupby(["Ano", "Mes", "instituicao"], as_index=False)["Saldo em Reais"]
        .sum()
        .rename(columns={"Saldo em Reais": "valor_poupanca"})
        .sort_values(["Ano", "Mes", "instituicao"])
    )
    estban["data_ref"] = estban["Ano"].astype(str) + "-" + estban["Mes"].astype(str).str.zfill(2)
    return estban


def maybe_clean_pipeline_outputs() -> None:
    """Remove CSVs gerados pelo pipeline antes de uma nova carga completa (sem 'banco' SQL)."""
    if os.environ.get("PIPELINE_CLEAN_OUTPUTS") != "1":
        return
    outputs = [
        BASE_DIR / "caged_botucatu_q1_2026.csv",
        BASE_DIR / "financas_botucatu_2026.csv",
        BASE_DIR / "estban_botucatu_2025_2026.csv",
        BASE_DIR / "caged_comparativo_municipios.csv",
        BASE_DIR / "relatorio_botucatu_q1_2026.csv",
        BASE_DIR / "investimentos_botucatu_2026.csv",
    ]
    for p in outputs:
        try:
            if p.exists():
                p.unlink()
                log(f"PIPELINE_CLEAN_OUTPUTS=1 — removido {p.name}")
        except OSError as exc:
            log(f"Aviso: não foi possível remover {p}: {exc}")


def export_outputs(caged: pd.DataFrame, siconfi: pd.DataFrame, caged_comp: pd.DataFrame) -> None:
    out_caged = BASE_DIR / "caged_botucatu_q1_2026.csv"
    out_fin = BASE_DIR / "financas_botucatu_2026.csv"
    out_estban = BASE_DIR / "estban_botucatu_2025_2026.csv"
    out_comp = BASE_DIR / "caged_comparativo_municipios.csv"
    out_caged_app = BASE_DIR / "relatorio_botucatu_q1_2026.csv"
    out_fin_app = BASE_DIR / "investimentos_botucatu_2026.csv"

    caged = enrich_caged_with_cnae_descriptions(caged)
    caged.to_csv(out_caged, sep=";", encoding="utf-8-sig", index=False)
    caged_comp.to_csv(out_comp, sep=";", encoding="utf-8-sig", index=False)
    siconfi.to_csv(out_fin, sep=";", encoding="utf-8-sig", index=False, decimal=",")
    estban = build_estban_like_dataset(siconfi)
    estban.to_csv(out_estban, sep=";", encoding="utf-8-sig", index=False, decimal=",")

    # Compatibilidade com app Streamlit já criado no projeto
    caged.to_csv(out_caged_app, sep=";", encoding="utf-8-sig", index=False)
    fin_app = siconfi.rename(
        columns={
            "Ano": "Ano",
            "Mês": "Mes",
            "Código Contábil": "Codigo_Contabil",
            "Saldo em Reais": "Saldo_em_Reais",
        }
    )
    fin_app.to_csv(out_fin_app, sep=";", encoding="utf-8-sig", index=False, decimal=",")

    log(f"Exportação CAGED concluída: {out_caged.name} ({len(caged)} linhas)")
    log(f"Exportação comparativo CAGED concluída: {out_comp.name} ({len(caged_comp)} linhas)")
    log(f"Exportação Finanças concluída: {out_fin.name} ({len(siconfi)} linhas)")
    log(f"Exportação ESTBAN-like concluída: {out_estban.name} ({len(estban)} linhas)")
    log("Arquivos de compatibilidade do app também foram gerados na raiz do projeto.")


def main() -> None:
    start = time.time()
    log("Pipeline Botucatu iniciado.")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    maybe_clean_pipeline_outputs()

    try:
        caged, caged_comp = run_caged_etl()
        siconfi = run_siconfi_etl()
        export_outputs(caged, siconfi, caged_comp)
        if os.environ.get("PIPELINE_INCLUDE_CNPJ") == "1":
            from cnpj_botucatu_etl import cleanup_workdir as cleanup_cnpj_workdir
            from cnpj_botucatu_etl import export_cnpj_csvs, run_cnpj_botucatu_etl

            log("PIPELINE_INCLUDE_CNPJ=1 — iniciando ETL Cadastro CNPJ / MEI (pode levar vários minutos e ~4GB download).")
            try:
                cnpj_dfs = run_cnpj_botucatu_etl()
                export_cnpj_csvs(cnpj_dfs)
            finally:
                cleanup_cnpj_workdir()
                log("Limpeza do diretório temporário CNPJ concluída.")
        elapsed = time.time() - start
        log(f"Pipeline finalizado com sucesso em {elapsed:.1f}s.")
    finally:
        # Garantia final de limpeza do diretório temporário
        if WORK_DIR.exists():
            shutil.rmtree(WORK_DIR, ignore_errors=True)
            log("Diretório temporário removido.")


if __name__ == "__main__":
    main()
