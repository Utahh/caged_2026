import json
import os
import shutil
import time
import urllib.request
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import py7zr
import requests


BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "tmp_pipeline_botucatu"
MONTHS = ["01", "02", "03"]
YEAR = "2026"
FINANCIAL_YEARS = ["2025", "2026"]
BOTUCATU_MUNICIPIO_CAGED = 350750
BOTUCATU_ID_ENTE_SICONFI = 3507506
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


def resolve_caged_columns(raw_file: Path, delimiter: str) -> Tuple[Dict[str, str], List[str]]:
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

    return {
        "municipio": col_municipio,
        "secao": col_secao,
        "subclasse": col_subclasse,
        "saldo": col_saldo,
    }, [col_municipio, col_secao, col_subclasse, col_saldo]


def process_caged_month(month: str) -> pd.DataFrame:
    month_url = f"ftp://ftp.mtps.gov.br/pdet/microdados/NOVO%20CAGED/{YEAR}/{YEAR}{month}/CAGEDMOV{YEAR}{month}.7z"
    archive_path = WORK_DIR / f"CAGEDMOV{YEAR}{month}.7z"
    extract_dir = WORK_DIR / f"extract_{month}"

    log(f"Iniciando CAGED mês {month}: download do FTP.")
    download_file(month_url, archive_path)
    log(f"Download concluído: {archive_path.name}")

    extracted_files = extract_7z(archive_path, extract_dir)
    txt_files = [p for p in extracted_files if p.suffix.lower() in (".txt", ".csv")]
    if not txt_files:
        raise RuntimeError(f"Nenhum TXT/CSV extraído do arquivo {archive_path.name}")

    raw_file = txt_files[0]
    delimiter = detect_delimiter(raw_file)
    log(f"Processando arquivo {raw_file.name} com delimitador '{delimiter}' em chunks de 100.000 linhas.")

    col_map, usecols = resolve_caged_columns(raw_file, delimiter)
    frames = []

    for idx, chunk in enumerate(
        pd.read_csv(
            raw_file,
            sep=delimiter,
            encoding="utf-8",
            chunksize=100_000,
            usecols=usecols,
            low_memory=False,
        ),
        start=1,
    ):
        chunk[col_map["municipio"]] = pd.to_numeric(chunk[col_map["municipio"]], errors="coerce").fillna(0).astype(int)
        filtered = chunk[chunk[col_map["municipio"]] == BOTUCATU_MUNICIPIO_CAGED].copy()
        if filtered.empty:
            if idx % 10 == 0:
                log(f"Mês {month}: chunk {idx} processado (sem linhas de Botucatu).")
            continue

        filtered["mes_referencia"] = int(month)
        filtered["saldomovimentacao"] = pd.to_numeric(filtered[col_map["saldo"]], errors="coerce").fillna(0).astype(int)
        filtered["admissao"] = (filtered["saldomovimentacao"] == 1).astype(int)
        filtered["demissao"] = (filtered["saldomovimentacao"] == -1).astype(int)

        month_df = pd.DataFrame(
            {
                "mes_referencia": filtered["mes_referencia"],
                "secao": filtered[col_map["secao"]].astype(str).str.strip(),
                "subclasse": filtered[col_map["subclasse"]].astype(str).str.strip(),
                "saldomovimentacao": filtered["saldomovimentacao"],
                "admissao": filtered["admissao"],
                "demissao": filtered["demissao"],
            }
        )
        frames.append(month_df)

        if idx % 10 == 0:
            log(f"Mês {month}: chunk {idx} processado, linhas acumuladas: {sum(len(f) for f in frames)}")

    # Limpeza obrigatória do disco após cada mês
    try:
        if archive_path.exists():
            archive_path.unlink()
        if raw_file.exists():
            raw_file.unlink()
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
    except Exception as cleanup_exc:
        log(f"Aviso de limpeza mês {month}: {cleanup_exc}")

    if not frames:
        return pd.DataFrame(
            columns=["mes_referencia", "secao", "subclasse", "saldomovimentacao", "admissao", "demissao"]
        )

    month_all = pd.concat(frames, ignore_index=True)
    return month_all


def run_caged_etl() -> pd.DataFrame:
    log("ETL CAGED iniciado.")
    monthly_results = []
    for m in MONTHS:
        try:
            month_df = process_caged_month(m)
            log(f"CAGED mês {m} finalizado. Linhas de Botucatu: {len(month_df)}")
            monthly_results.append(month_df)
        except Exception as exc:
            log(f"Erro ao processar mês {m}: {exc}")

    if not monthly_results:
        raise RuntimeError("Nenhum mês CAGED foi processado com sucesso.")

    caged = pd.concat(monthly_results, ignore_index=True)
    if caged.empty:
        log("CAGED consolidado vazio para Botucatu.")
        return caged

    caged = (
        caged.groupby(["mes_referencia", "secao", "subclasse"], as_index=False)[
            ["saldomovimentacao", "admissao", "demissao"]
        ]
        .sum()
        .sort_values(["mes_referencia", "secao", "subclasse"])
    )
    caged["subclasse"] = caged["subclasse"].map(normalize_subclasse_code)
    caged["estoque_anual_2026"] = caged.groupby(["secao", "subclasse"])["saldomovimentacao"].transform("sum")
    return caged


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


def export_outputs(caged: pd.DataFrame, siconfi: pd.DataFrame) -> None:
    out_caged = BASE_DIR / "caged_botucatu_q1_2026.csv"
    out_fin = BASE_DIR / "financas_botucatu_2026.csv"
    out_estban = BASE_DIR / "estban_botucatu_2025_2026.csv"
    out_caged_app = BASE_DIR / "relatorio_botucatu_q1_2026.csv"
    out_fin_app = BASE_DIR / "investimentos_botucatu_2026.csv"

    caged = enrich_caged_with_cnae_descriptions(caged)
    caged.to_csv(out_caged, sep=";", encoding="utf-8-sig", index=False)
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
    log(f"Exportação Finanças concluída: {out_fin.name} ({len(siconfi)} linhas)")
    log(f"Exportação ESTBAN-like concluída: {out_estban.name} ({len(estban)} linhas)")
    log("Arquivos de compatibilidade do app também foram gerados na raiz do projeto.")


def main() -> None:
    start = time.time()
    log("Pipeline Botucatu iniciado.")
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    try:
        caged = run_caged_etl()
        siconfi = run_siconfi_etl()
        export_outputs(caged, siconfi)
        elapsed = time.time() - start
        log(f"Pipeline finalizado com sucesso em {elapsed:.1f}s.")
    finally:
        # Garantia final de limpeza do diretório temporário
        if WORK_DIR.exists():
            shutil.rmtree(WORK_DIR, ignore_errors=True)
            log("Diretório temporário removido.")


if __name__ == "__main__":
    main()
