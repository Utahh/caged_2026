import ftplib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import hashlib
import pandas as pd
import py7zr
import requests

from caged_eventos import (
    build_exec_meta_v2,
    finalize_caged_botucatu_layers,
    finalize_caged_comp_municipios_layers,
    staging_dir,
    write_exec_meta,
)


BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "tmp_pipeline_botucatu"
# Sobrescreva para retomar após interrupção (ex.: PIPELINE_CAGED_START_YEAR=2025 PIPELINE_CAGED_START_MONTH=4)
START_YEAR = int(os.environ.get("PIPELINE_CAGED_START_YEAR", "2024"))
START_MONTH = int(os.environ.get("PIPELINE_CAGED_START_MONTH", "1"))
# Microdados no FTP costumam atrasar em relação ao mês civil (ex.: em maio/2026 ainda não há MOV de abril).
FTP_LAG_MONTHS = max(0, int(os.environ.get("PIPELINE_CAGED_FTP_LAG_MONTHS", "2")))
# Descobre no FTP a última competência com CAGEDMOV publicado (padrão: ligado). Desligar: PIPELINE_CAGED_FTP_DISCOVER=0
FTP_DISCOVER = os.environ.get("PIPELINE_CAGED_FTP_DISCOVER", "1").strip().lower() not in ("0", "false", "off", "no")
FTP_MAX_BACKTRACK = max(1, int(os.environ.get("PIPELINE_CAGED_FTP_MAX_BACKTRACK", "48")))
FTP_HOST = "ftp.mtps.gov.br"
# Deduplica linhas com o mesmo id de movimentação antes do groupby (desligar: PIPELINE_CAGED_DEDUPE_ID=0).
CAGED_DEDUPE_BY_ID = os.environ.get("PIPELINE_CAGED_DEDUPE_ID", "1").strip().lower() not in ("0", "false", "off", "no")
CAGED_PREFIX_RANK = {"CAGEDMOV": 0, "CAGEDFOR": 1, "CAGEDEXC": 2}
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


def normalize_municipio_caged_series(series: pd.Series) -> pd.Series:
    """
    Alinha o código de município do Novo CAGED ao formato de 6 dígitos usado nos recortes do projeto.

    O IBGE divulga código de 7 dígitos (inclui dígito verificador). Em muitos arquivos do CAGED o campo
    já vem com 6 posições; quando vem com 7, o sétimo dígito é removido (divisão inteira por 10) para
    casar com `BOTUCATU_MUNICIPIO_CAGED` e `MUNICIPIOS_COMPARATIVO_CAGED`.
    """
    m = pd.to_numeric(series, errors="coerce").fillna(0).astype("int64")
    return m.where(m < 1_000_000, m // 10).astype("int64")


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


def _subtract_months(year: int, month: int, n: int) -> Tuple[int, int]:
    y, m = year, month
    for _ in range(n):
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
    return y, m


def caged_inclusive_end_period(today: date, lag_months: int) -> Tuple[int, int]:
    """Último (ano, mês) inclusive a pedir no FTP (evita 550 em meses ainda não publicados)."""
    return _subtract_months(today.year, today.month, lag_months)


def _ftp_remote_has_caged_mov(ftp: ftplib.FTP, year: int, month: int) -> bool:
    """True se existir CAGEDMOVAAAAMM.7z na pasta oficial da competência."""
    month_txt = f"{month:02d}"
    fname = f"CAGEDMOV{year}{month_txt}.7z"
    segments = ("pdet", "microdados", "NOVO CAGED", str(year), f"{year}{month_txt}")
    try:
        ftp.cwd("/")
        for seg in segments:
            ftp.cwd(seg)
        try:
            sz = ftp.size(fname)
        except (ftplib.error_perm, ftplib.error_temp, OSError):
            sz = None
        if sz is not None:
            try:
                return int(sz) >= 0
            except (TypeError, ValueError):
                return True
        return fname in ftp.nlst()
    except (ftplib.error_perm, ftplib.error_temp, OSError):
        return False


def discover_latest_caged_mov_month() -> Optional[Tuple[int, int]]:
    """
    Retrocede a partir do mês civil atual até achar a última competência com CAGEDMOV no FTP.
    Cada execução alinha o processamento ao que está publicado (histórico vivo).
    """
    y, m = CURRENT_DATE.year, CURRENT_DATE.month
    try:
        with ftplib.FTP(FTP_HOST, encoding="utf-8", timeout=60) as ftp:
            ftp.login()
            for step in range(1, FTP_MAX_BACKTRACK + 1):
                if _ftp_remote_has_caged_mov(ftp, y, m):
                    if step > 1:
                        log(
                            f"FTP: última competência com CAGEDMOV publicada = {y}-{m:02d} "
                            f"(retrocedeu {step - 1} mês(es) a partir de {CURRENT_DATE.year}-{CURRENT_DATE.month:02d})."
                        )
                    else:
                        log(f"FTP: última competência com CAGEDMOV publicada = {y}-{m:02d}.")
                    return (y, m)
                y, m = _subtract_months(y, m, 1)
    except Exception as exc:
        log(f"Aviso: varredura FTP para última competência falhou ({exc}); usando limite por lag.")
        return None
    log(f"Aviso: CAGEDMOV não encontrado nos últimos {FTP_MAX_BACKTRACK} meses no FTP; usando limite por lag.")
    return None


def _caged_ftp_inclusive_end() -> Tuple[int, int]:
    if FTP_DISCOVER:
        discovered = discover_latest_caged_mov_month()
        if discovered is not None:
            return discovered
    return caged_inclusive_end_period(CURRENT_DATE, FTP_LAG_MONTHS)


def build_caged_periods(start_year: int, start_month: int) -> List[Tuple[int, int]]:
    end_y, end_m = _caged_ftp_inclusive_end()
    cap_y = os.environ.get("PIPELINE_CAGED_END_YEAR")
    cap_m = os.environ.get("PIPELINE_CAGED_END_MONTH")
    if cap_y is not None and cap_m is not None:
        ey, em = int(cap_y), int(cap_m)
        if (ey, em) < (end_y, end_m):
            end_y, end_m = ey, em
    periods: List[Tuple[int, int]] = []
    y, m = start_year, start_month
    while (y, m) <= (end_y, end_m):
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


def resolve_caged_columns(raw_file: Path, delimiter: str) -> Tuple[Dict[str, str], List[str], Dict[str, object], Optional[str]]:
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

    col_competencia = first_present(
        col_map,
        [
            "competenciamov",
            "competenciamovimentacao",
            "competencia_mov",
            "competenciadamovimentacao",
            "competencia",
            "competncia",
        ],
    )
    col_ano_mov = first_present(
        col_map,
        [
            "anocompetenciamov",
            "ano_competencia_movimento",
            "ano_competencia_mov",
            "ano_competencia",
            "ano_movimento",
            "ano_movimentacao",
        ],
    )
    col_mes_mov = first_present(
        col_map,
        [
            "mescompetenciamov",
            "mes_competencia_movimento",
            "mes_competencia_mov",
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

    id_mov = first_present(
        col_map,
        [
            "id",
            "idunico",
            "idunicomov",
            "id_movimentacao",
            "idmovimentacao",
            "identificadordemovimentacao",
            "identificador",
        ],
    )

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
    return base_map, usecols, period_spec, id_mov


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


def _period_spec_rank(spec: Dict[str, object]) -> int:
    return {"folder": 0, "ano_mes": 1, "competencia": 2}.get(str(spec.get("mode", "folder")), 0)


def _period_spec_readable_on_columns(period_spec: Dict[str, object], columns: Iterable[str]) -> bool:
    colset = set(columns)
    mode = str(period_spec.get("mode", "folder"))
    if mode == "folder":
        return True
    if mode == "competencia":
        return str(period_spec["col"]) in colset
    if mode == "ano_mes":
        return str(period_spec["ano"]) in colset and str(period_spec["mes"]) in colset
    return True


def _period_spec_key(ps: Dict[str, object]) -> Tuple[object, ...]:
    mode = str(ps.get("mode", "folder"))
    if mode == "competencia":
        return ("competencia", str(ps.get("col")))
    if mode == "ano_mes":
        return ("ano_mes", str(ps.get("ano")), str(ps.get("mes")))
    return ("folder",)


def _unify_period_spec_across_caged_files(
    stages: List[Dict[str, object]],
) -> Dict[str, object]:
    """
    Escolhe o modo de competência mais informativo que exista em **todos** os arquivos baixados
    (MOV ∪ FOR ∪ EXC), para retificações FOR alocarem no mês do evento e não só no mês da pasta.
    """
    pairs: List[Tuple[Dict[str, object], List[str]]] = [
        (s["period_spec"], s["header_columns"]) for s in stages if s.get("period_spec")
    ]
    if not pairs:
        return {"mode": "folder"}
    by_key: Dict[Tuple[object, ...], Dict[str, object]] = {}
    for ps, _ in pairs:
        by_key[_period_spec_key(ps)] = ps
    candidates = list(by_key.values())
    best: Optional[Dict[str, object]] = None
    best_r = -1
    for ps in candidates:
        r = _period_spec_rank(ps)
        if r < best_r:
            continue
        if all(_period_spec_readable_on_columns(ps, h) for _, h in pairs):
            if r > best_r or best is None:
                best_r = r
                best = ps
    return best if best is not None else {"mode": "folder"}


def _usecols_for_processing(
    base_map: Dict[str, str], period_spec: Dict[str, object], id_col: Optional[str]
) -> List[str]:
    usecols = [base_map["municipio"], base_map["secao"], base_map["subclasse"], base_map["saldo"]]
    mode = str(period_spec.get("mode", "folder"))
    if mode == "competencia":
        usecols.append(str(period_spec["col"]))
    elif mode == "ano_mes":
        usecols.append(str(period_spec["ano"]))
        usecols.append(str(period_spec["mes"]))
    if id_col:
        usecols.append(str(id_col))
    return list(dict.fromkeys(usecols))


def choose_caged_movement_id_column(stages: List[Dict[str, object]]) -> Optional[str]:
    """Mesmo nome físico de id em todos os arquivos da competência (MOV/FOR/EXC), ou None."""
    ids = [s.get("id_mov") for s in stages]
    if not ids or not all(ids):
        return None
    first = ids[0]
    if all(i == first for i in ids):
        return str(first)
    log("Aviso deduplicação: coluna de id distinta entre MOV/FOR/EXC nesta competência; dedupe por id desativada.")
    return None


def _strip_caged_dedup_aux_columns(df: pd.DataFrame) -> pd.DataFrame:
    dropme = ("__caged_mov_id", "__ftp_decl_y", "__ftp_decl_m", "__caged_src_rank")
    return df.drop(columns=[c for c in dropme if c in df.columns], errors="ignore")


def dedupe_caged_micro_rows_before_agg(df: pd.DataFrame, *, strip_aux: bool = True) -> pd.DataFrame:
    """
    Evita contar duas vezes a mesma movimentação se ela reaparecer entre MOV/FOR/EXC ou entre
    competências de declaração (pasta FTP), mantendo a ocorrência mais recente.

    strip_aux: se False, mantém colunas de proveniência (__caged_mov_id, pasta FTP, rank MOV/FOR/EXC)
    para export na camada de eventos (`caged_eventos.finalize_caged_botucatu_layers`).
    """
    if not CAGED_DEDUPE_BY_ID or df.empty or "__caged_mov_id" not in df.columns:
        return _strip_caged_dedup_aux_columns(df) if strip_aux else df
    key = df["__caged_mov_id"].astype(str).str.strip()
    has_id = key.ne("") & key.notna()
    if not has_id.any():
        return _strip_caged_dedup_aux_columns(df) if strip_aux else df
    n0 = len(df)
    sort_cols = ["ano_referencia", "mes_referencia", "__ftp_decl_y", "__ftp_decl_m", "__caged_src_rank"]
    dedupe_subset = ["__caged_mov_id"]
    if "municipio_codigo" in df.columns:
        dedupe_subset = ["__caged_mov_id", "municipio_codigo"]
    d1 = df[has_id].sort_values(sort_cols, kind="mergesort").drop_duplicates(subset=dedupe_subset, keep="last")
    d2 = df[~has_id]
    out = pd.concat([d1, d2], ignore_index=True)
    if len(out) < n0:
        log(
            f"Deduplicação CAGED: removidas {n0 - len(out)} linhas com mesmo id de movimentação "
            "(mantida a declaração mais recente por pasta FTP e prioridade EXC>FOR>MOV)."
        )
    return _strip_caged_dedup_aux_columns(out) if strip_aux else out


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


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Hash SHA-256 do arquivo no disco (streaming)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def process_caged_month(year: int, month: int) -> Tuple[pd.DataFrame, pd.DataFrame, List[Dict[str, Any]]]:
    """
    Consolida MOV + FOR + EXC da **mesma** competência (mesma pasta FTP / mesmo snapshot).

    Regra para não inflar totais:
    - Baixar todos os .7z disponíveis da pasta `.../NOVO CAGED/AAAA/AAAAMM/` na mesma execução.
    - Cada linha é contada **uma vez**; o saldo oficial é a soma de `saldomovimentacao` sobre a união
      das linhas (MOV ∪ FOR ∪ EXC). O MTPE organiza os arquivos para que não se some o mesmo
      movimento duas vezes entre arquivos da **mesma** publicação.
    - Não misturar MOV de um mês de download com FOR de outro: isso sim geraria duplicidade.
    - Se o layout trouxer **id de movimentação** idêntico em MOV/FOR/EXC, deduplica-se antes do
      `groupby` global (mantém a ocorrência mais recente por pasta de declaração e EXC>FOR>MOV).

    Competência do **evento** (Novo CAGED): após baixar MOV/FOR/EXC, escolhe-se o melhor modo de período
    (`competencia` AAAAMM, par ano/mês, ou pasta) que seja **legível em todos** os arquivos, para que
    retificações FOR movam totais para o mês correto do evento. O histórico é **vivo**: cada execução
    reprocessa o intervalo e sobrescreve os CSVs com o snapshot atual do FTP.

    Retorna também a lista `ftp_fontes_7z` (SHA-256 e tamanho de cada .7z baixado nesta competência).
    A agregação em fatos e o `exec_meta.json` ficam em `caged_eventos`.
    """
    month_txt = f"{month:02d}"
    year_txt = str(year)
    base_url = f"ftp://ftp.mtps.gov.br/pdet/microdados/NOVO%20CAGED/{year_txt}/{year_txt}{month_txt}/"

    fontes: List[Tuple[str, bool]] = [
        ("CAGEDMOV", True),
        ("CAGEDFOR", False),
        ("CAGEDEXC", False),
    ]
    ftp_snapshots_mes: List[Dict[str, Any]] = []

    frames: List[pd.DataFrame] = []
    frames_comp: List[pd.DataFrame] = []
    stages: List[Dict[str, object]] = []

    log(
        f"Iniciando CAGED competência {year_txt}-{month_txt}: "
        "MOV (obrigatório) + FOR/EXC quando existirem, mesmo diretório FTP (snapshot único)."
    )

    # Fase 1: baixar e inspecionar cabeçalhos (para unificar competência entre MOV/FOR/EXC).
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
        try:
            digest = _sha256_file(archive_path)
            ftp_snapshots_mes.append(
                {
                    "competencia_pasta_ftp": f"{year}-{month:02d}",
                    "prefixo": prefix,
                    "arquivo": fname,
                    "sha256": digest,
                    "bytes": int(archive_path.stat().st_size),
                }
            )
        except OSError as hex_exc:
            log(f"Aviso: SHA-256 de {fname} não calculado: {hex_exc}")

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
        header_df = pd.read_csv(raw_file, sep=delim, nrows=0, encoding="utf-8", low_memory=False)
        header_columns = list(header_df.columns)
        col_map, _usecols_init, period_spec, id_mov = resolve_caged_columns(raw_file, delim)
        stages.append(
            {
                "prefix": prefix,
                "obrigatorio": obrigatorio,
                "raw_file": raw_file,
                "delim": delim,
                "header_columns": header_columns,
                "col_map": col_map,
                "period_spec": period_spec,
                "id_mov": id_mov,
                "archive_path": archive_path,
                "extract_dir": extract_dir,
            }
        )

    if not stages:
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
            ftp_snapshots_mes,
        )

    id_col = choose_caged_movement_id_column(stages)
    period_spec_final = _unify_period_spec_across_caged_files(stages)
    ps_mode = str(period_spec_final.get("mode", "folder"))
    if ps_mode == "folder":
        log(
            f"Aviso layout CAGED: competência da movimentação não unificável entre arquivos; "
            f"fallback mês da pasta FTP ({year_txt}-{month_txt}). "
            "Com layout completo, retificações FOR entram no mês do evento (Novo CAGED)."
        )
    else:
        log(
            f"Período da movimentação (Novo CAGED): modo '{ps_mode}' aplicado a MOV/FOR/EXC desta competência."
        )

    logged_layout = False

    # Fase 2: processar chunks com o mesmo period_spec_final em todos os arquivos.
    for s in stages:
        prefix = str(s["prefix"])
        raw_file = s["raw_file"]
        delim = str(s["delim"])
        col_map = s["col_map"]
        archive_path = s["archive_path"]
        extract_dir = s["extract_dir"]
        usecols = _usecols_for_processing(col_map, period_spec_final, id_col)
        src_rank = int(CAGED_PREFIX_RANK.get(prefix, 99))

        if not logged_layout:
            log(
                f"Layout CAGED ({prefix}): delimitador '{delim}', "
                f"processando em chunks de 100.000 linhas — arquivo {raw_file.name}."
            )
            logged_layout = True

        for idx, chunk in enumerate(
            pd.read_csv(
                raw_file,
                sep=delim,
                encoding="utf-8",
                chunksize=100_000,
                usecols=usecols,
                low_memory=False,
            ),
            start=1,
        ):
            chunk[col_map["municipio"]] = normalize_municipio_caged_series(chunk[col_map["municipio"]])
            filtered = chunk[chunk[col_map["municipio"]] == BOTUCATU_MUNICIPIO_CAGED].copy()
            if filtered.empty:
                if idx % 10 == 0:
                    log(f"{prefix} {year_txt}-{month_txt}: chunk {idx} (sem linhas de Botucatu).")
                continue

            assign_movimento_period(filtered, period_spec_final, year, month)
            filtered["saldomovimentacao"] = pd.to_numeric(filtered[col_map["saldo"]], errors="coerce").fillna(
                0
            ).astype(int)
            # Novo CAGED: saldo pode ser ±2, ±3… (mais de uma vaga por linha). Admissão = massa positiva; desligamento = massa negativa.
            sm = filtered["saldomovimentacao"]
            filtered["admissao"] = sm.clip(lower=0).astype(int)
            filtered["demissao"] = (-sm.clip(upper=0)).astype(int)

            row_bot: Dict[str, object] = {
                "ano_referencia": filtered["ano_referencia"],
                "mes_referencia": filtered["mes_referencia"],
                "secao": filtered[col_map["secao"]].astype(str).str.strip(),
                "subclasse": filtered[col_map["subclasse"]].astype(str).str.strip(),
                "saldomovimentacao": filtered["saldomovimentacao"],
                "admissao": filtered["admissao"],
                "demissao": filtered["demissao"],
            }
            if id_col:
                row_bot["__caged_mov_id"] = filtered[id_col].map(lambda x: "" if pd.isna(x) else str(x).strip())
                row_bot["__ftp_decl_y"] = year
                row_bot["__ftp_decl_m"] = month
                row_bot["__caged_src_rank"] = src_rank
            month_df = pd.DataFrame(row_bot)
            frames.append(month_df)

            filtered_comp = chunk[chunk[col_map["municipio"]].isin(MUNICIPIOS_COMPARATIVO_CAGED.keys())].copy()
            if not filtered_comp.empty:
                assign_movimento_period(filtered_comp, period_spec_final, year, month)
                filtered_comp["saldomovimentacao"] = (
                    pd.to_numeric(filtered_comp[col_map["saldo"]], errors="coerce").fillna(0).astype(int)
                )
                row_comp: Dict[str, object] = {
                    "ano_referencia": filtered_comp["ano_referencia"],
                    "mes_referencia": filtered_comp["mes_referencia"],
                    "municipio_codigo": filtered_comp[col_map["municipio"]].astype(int),
                    "Saldo": filtered_comp["saldomovimentacao"],
                }
                if id_col:
                    row_comp["__caged_mov_id"] = filtered_comp[id_col].map(
                        lambda x: "" if pd.isna(x) else str(x).strip()
                    )
                    row_comp["__ftp_decl_y"] = year
                    row_comp["__ftp_decl_m"] = month
                    row_comp["__caged_src_rank"] = src_rank
                comp_df = pd.DataFrame(row_comp)
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
            ftp_snapshots_mes,
        )

    month_all = pd.concat(frames, ignore_index=True)
    month_comp = (
        pd.concat(frames_comp, ignore_index=True)
        if frames_comp
        else pd.DataFrame(columns=["ano_referencia", "mes_referencia", "municipio_codigo", "Saldo"])
    )
    return month_all, month_comp, ftp_snapshots_mes


def run_caged_etl() -> Tuple[pd.DataFrame, pd.DataFrame]:
    log("ETL CAGED iniciado.")
    periods = build_caged_periods(START_YEAR, START_MONTH)
    if periods:
        last_y, last_m = periods[-1]
        tail = (
            "último mês = varredura FTP da competência mais recente com CAGEDMOV "
            "(desative com PIPELINE_CAGED_FTP_DISCOVER=0 e use só lag). "
        )
        if not FTP_DISCOVER:
            tail = f"último mês = mês civil menos {FTP_LAG_MONTHS} (PIPELINE_CAGED_FTP_LAG_MONTHS). "
        log(
            f"CAGED: {len(periods)} competência(ões) de {START_YEAR}-{START_MONTH:02d} até {last_y}-{last_m:02d}; "
            f"{tail}"
            "Teto opcional: PIPELINE_CAGED_END_YEAR/MONTH. Agregação pelo mês do evento quando o layout permitir."
        )
    else:
        log("CAGED: nenhum período no intervalo (verifique START_* e PIPELINE_CAGED_FTP_LAG_MONTHS / END_*).")
    monthly_results = []
    comp_results = []
    all_ftp_snaps: List[Dict[str, Any]] = []
    for year, month in periods:
        try:
            month_df, month_comp, snaps = process_caged_month(year, month)
            all_ftp_snaps.extend(snaps)
            log(f"CAGED mês {year}-{month:02d} finalizado. Linhas de Botucatu: {len(month_df)}")
            monthly_results.append(month_df)
            if not month_comp.empty:
                comp_results.append(month_comp)
        except Exception as exc:
            err = str(exc).lower()
            if "550" in err or "not found" in err or "no such file" in err:
                log(
                    f"Aviso: microdado CAGED {year}-{month:02d} ainda indisponível no FTP (ou URL incorreta): {exc}"
                )
            else:
                log(f"Erro ao processar mês {year}-{month:02d}: {exc}")

    if not monthly_results:
        raise RuntimeError("Nenhum mês CAGED foi processado com sucesso.")

    caged_raw = pd.concat(monthly_results, ignore_index=True)
    if caged_raw.empty:
        log("CAGED consolidado vazio para Botucatu.")
        return caged_raw, pd.DataFrame(columns=["ano_referencia", "mes_referencia", "Municipio", "Saldo"])

    caged_micro = dedupe_caged_micro_rows_before_agg(caged_raw, strip_aux=False)
    caged, bot_info = finalize_caged_botucatu_layers(caged_micro)

    if comp_results:
        comp_raw = pd.concat(comp_results, ignore_index=True)
        comp_micro = dedupe_caged_micro_rows_before_agg(comp_raw, strip_aux=False)
        comp, comp_info = finalize_caged_comp_municipios_layers(comp_micro)
    else:
        comp, comp_info = finalize_caged_comp_municipios_layers(
            pd.DataFrame(columns=["ano_referencia", "mes_referencia", "municipio_codigo", "Saldo"])
        )

    if "municipio_codigo" in comp.columns:
        comp = comp.copy()
        comp["Municipio"] = comp["municipio_codigo"].map(MUNICIPIOS_COMPARATIVO_CAGED).fillna("Outros")
        comp = comp[["ano_referencia", "mes_referencia", "Municipio", "Saldo"]]
    else:
        comp = pd.DataFrame(columns=["ano_referencia", "mes_referencia", "Municipio", "Saldo"])

    meta = build_exec_meta_v2(
        periods=periods,
        dedupe_por_id=CAGED_DEDUPE_BY_ID,
        ftp_fontes_7z=all_ftp_snaps,
        botucatu=bot_info,
        comparativo_municipios=comp_info,
    )
    write_exec_meta(staging_dir(), meta)
    log(
        f"CAGED staging exec_meta.json: Botucatu micro={bot_info['linhas_micro_pos_dedupe']}, "
        f"fato={bot_info['linhas_fato_mensal']}; comparativo micro={comp_info['linhas_micro_pos_dedupe']}, "
        f"fato={comp_info['linhas_fato_mes_municipio']}; registros_sha256_7z={len(all_ftp_snaps)}."
    )
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
        BASE_DIR / "data" / "caged_staging" / "exec_meta.json",
        BASE_DIR / "data" / "caged_staging" / "eventos_botucatu_micro.csv.gz",
        BASE_DIR / "data" / "caged_staging" / "eventos_comparativo_municipios_micro.csv.gz",
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
        if os.environ.get("PIPELINE_INCLUDE_COMEX") == "1":
            from comexstat_botucatu_etl import export_comex_csvs

            log(
                "PIPELINE_INCLUDE_COMEX=1 — ETL Comex Stat + PTAX (vários minutos; API MDIC pode limitar taxa de requisições)."
            )
            export_comex_csvs(BASE_DIR)
        if os.environ.get("PIPELINE_INCLUDE_COMEX_RASTREABILIDADE", "1") != "0":
            import sys

            sys.path.insert(0, str(BASE_DIR / "src"))
            from observatorio_comex.integration import run_rastreabilidade_botucatu

            log("Rastreabilidade SH4×CNAE×CNPJ (CSV local)…")
            try:
                fact = run_rastreabilidade_botucatu(BASE_DIR)
                log(f"Fato SH4×empresas: {len(fact)} linhas → data/processed/fato_sh4_empresas_botucatu.csv")
            except Exception as exc:
                log(f"Aviso rastreabilidade Comex×CNPJ: {exc}")
        elapsed = time.time() - start
        log(f"Pipeline finalizado com sucesso em {elapsed:.1f}s.")
    finally:
        # Garantia final de limpeza do diretório temporário
        if WORK_DIR.exists():
            shutil.rmtree(WORK_DIR, ignore_errors=True)
            log("Diretório temporário removido.")


if __name__ == "__main__":
    main()
