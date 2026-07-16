"""Importadores dos relatórios consolidados do Domínio e do extrato PGDAS-D."""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd


class DominioImportError(ValueError):
    """Erro de layout ou conteúdo em arquivo Domínio/PGDAS."""


@dataclass(frozen=True)
class DominioSimulationReport:
    empresa: str
    cnpj: str
    periodo: pd.Timestamp
    tributos_atuais: dict[str, float]
    fase_2027: dict[str, float]
    fase_2033: dict[str, float]
    base_saidas: float
    base_entradas_credito: float
    aliquota_cbs_2027: float
    aliquota_ibs_2027: float
    aliquota_cbs_2033: float
    aliquota_ibs_2033: float
    aliquota_credito_cbs_2027: float
    aliquota_credito_ibs_2027: float
    aliquota_credito_cbs_2033: float
    aliquota_credito_ibs_2033: float
    saidas_por_acumulador: pd.DataFrame
    entradas_por_acumulador: pd.DataFrame
    clientes_por_regime: pd.DataFrame
    fornecedores_por_regime: pd.DataFrame
    vendas_nao_contribuinte: float
    percentual_operacoes_creditaveis: float


@dataclass(frozen=True)
class MonthlyReport:
    empresa: str
    cnpj: str
    periodo_inicial: pd.Timestamp
    periodo_final: pd.Timestamp
    movimentos: pd.DataFrame


@dataclass(frozen=True)
class PGDASReport:
    empresa: str
    cnpj_basico: str
    cnpj_estabelecimento: str
    periodo: str
    regime_apuracao: str
    rpa: float
    rbt12: float
    rba: float
    rbaa: float
    anexo: str
    atividade: str
    tributos: dict[str, float]
    total_das: float
    aliquota_efetiva: float
    receitas_anteriores: pd.DataFrame = field(compare=False)


def normalize_text(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).replace("�", "").strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def clean_document(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return re.sub(r"\D", "", text)


def parse_br_number(value: object) -> float:
    if pd.isna(value) or str(value).strip() == "":
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = re.sub(r"[^0-9,().-]", "", str(value))
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        result = float(text)
        return -result if negative else result
    except ValueError:
        return 0.0


def _open_legacy_workbook(content: bytes) -> pd.ExcelFile:
    """Abre XLS legado do Domínio usando Calamine, tolerante ao BIFF irregular."""
    try:
        return pd.ExcelFile(io.BytesIO(content), engine="calamine")
    except Exception as exc:
        raise DominioImportError(
            "O XLS não corresponde ao formato consolidado esperado do Domínio ou está corrompido."
        ) from exc


def _read_sheet(workbook: pd.ExcelFile, sheet_name: str) -> pd.DataFrame:
    return pd.read_excel(
        workbook,
        sheet_name=sheet_name,
        header=None,
        dtype=object,
        engine="calamine",
    )


def _find_sheet(workbook: pd.ExcelFile, term: str) -> str:
    match = next((name for name in workbook.sheet_names if term in normalize_text(name)), None)
    if match is None:
        raise DominioImportError(
            f"A aba '{term}' não foi encontrada. Abas disponíveis: {', '.join(workbook.sheet_names)}."
        )
    return match


def _row_text(frame: pd.DataFrame, row_index: int) -> str:
    return " ".join(normalize_text(value) for value in frame.iloc[row_index] if pd.notna(value))


def _find_row(frame: pd.DataFrame, *terms: str, start: int = 0) -> int:
    normalized_terms = [normalize_text(term) for term in terms]
    for index in range(start, len(frame)):
        text = _row_text(frame, index)
        if all(term in text for term in normalized_terms):
            return index
    raise DominioImportError(f"Trecho obrigatório não encontrado no relatório: {' / '.join(terms)}.")


def _find_row_optional(frame: pd.DataFrame, *terms: str, start: int = 0) -> int | None:
    """Localiza uma seção que o Domínio pode omitir quando não há movimento."""
    normalized_terms = [normalize_text(term) for term in terms]
    for index in range(start, len(frame)):
        text = _row_text(frame, index)
        if all(term in text for term in normalized_terms):
            return index
    return None


def _find_phase_values(frame: pd.DataFrame, year: int) -> tuple[int, dict[str, float]]:
    phase_row = _find_row(frame, str(year), "fase")
    for index in range(phase_row + 1, min(phase_row + 7, len(frame))):
        if (
            frame.shape[1] > 58
            and pd.notna(frame.iat[index, 38])
            and pd.notna(frame.iat[index, 58])
            and isinstance(frame.iat[index, 38], (int, float))
            and isinstance(frame.iat[index, 58], (int, float))
        ):
            values = {
                "simples_residual": parse_br_number(frame.iat[index, 41]),
                "cbs": parse_br_number(frame.iat[index, 48]),
                "ibs": parse_br_number(frame.iat[index, 52]),
                "total": parse_br_number(frame.iat[index, 58]),
                "diferenca": parse_br_number(frame.iat[index, 68]),
                "diferenca_percentual": parse_br_number(frame.iat[index, 74]) / 100,
            }
            return index, values
    raise DominioImportError(f"Valores do cenário {year} não foram localizados.")


def _extract_section_rows(
    frame: pd.DataFrame, start_row: int, end_row: int, value_column: int = 13
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for index in range(start_row + 1, end_row):
        label = frame.iat[index, 1] if frame.shape[1] > 1 else None
        value = frame.iat[index, value_column] if frame.shape[1] > value_column else None
        label_text = normalize_text(label)
        if not label_text or "empresa" in label_text or label_text.startswith("total") or pd.isna(value):
            continue
        record = {"Descrição": str(label).strip(), "Valor": parse_br_number(value)}
        if frame.shape[1] > 16 and pd.notna(frame.iat[index, 16]):
            record["Percentual"] = parse_br_number(frame.iat[index, 16]) / 100
        records.append(record)
    if records:
        return pd.DataFrame(records)
    return pd.DataFrame(columns=["Descrição", "Valor", "Percentual"])


def _parse_dominio_simulation_frames(
    summary: pd.DataFrame, detail: pd.DataFrame
) -> DominioSimulationReport:
    if summary.shape[1] < 75 or detail.shape[1] < 17:
        raise DominioImportError("O relatório de simulação possui menos colunas que o layout esperado.")

    empresa = str(summary.iat[0, 0]).strip()
    cnpj = clean_document(summary.iat[1, 4])
    periodo = pd.Timestamp(summary.iat[2, 4])
    _, phase_2027 = _find_phase_values(summary, 2027)
    current_row, phase_2033 = _find_phase_values(summary, 2033)
    current_values = {
        "IRPJ": parse_br_number(summary.iat[current_row, 1]),
        "CSLL": parse_br_number(summary.iat[current_row, 6]),
        "INSS/CPP": parse_br_number(summary.iat[current_row, 10]),
        "IPI": parse_br_number(summary.iat[current_row, 12]),
        "ICMS": parse_br_number(summary.iat[current_row, 20]),
        "ISS": parse_br_number(summary.iat[current_row, 24]),
        "PIS/Pasep": parse_br_number(summary.iat[current_row, 30]),
        "COFINS": parse_br_number(summary.iat[current_row, 34]),
        "Total": parse_br_number(summary.iat[current_row, 38]),
    }

    debit_row = _find_row(summary, "debitos", "saidas", start=current_row)
    # Empresas exclusivamente prestadoras podem não ter entradas na competência.
    # Nessa situação, o Domínio omite tanto a linha de créditos quanto a seção
    # detalhada de entradas; a ausência representa movimento zero, não layout inválido.
    credit_row = _find_row_optional(summary, "creditos", "entradas", start=debit_row)
    base_saidas = parse_br_number(summary.iat[debit_row, 8])
    base_entradas = parse_br_number(summary.iat[credit_row, 8]) if credit_row is not None else 0.0

    entradas_row = _find_row_optional(detail, "entradas")
    clientes_row = _find_row(detail, "clientes", start=entradas_row or 5)
    fornecedores_row = _find_row_optional(detail, "fornecedores", start=clientes_row)
    footer_row = len(detail) - 1
    saidas = _extract_section_rows(detail, 5, entradas_row or clientes_row)
    entradas = (
        _extract_section_rows(detail, entradas_row, clientes_row)
        if entradas_row is not None
        else pd.DataFrame(columns=["Descrição", "Valor", "Percentual"])
    )
    clientes = _extract_section_rows(detail, clientes_row, fornecedores_row or footer_row)
    fornecedores = (
        _extract_section_rows(detail, fornecedores_row, footer_row)
        if fornecedores_row is not None
        else pd.DataFrame(columns=["Descrição", "Valor", "Percentual"])
    )

    non_taxpayer_sales = float(
        saidas.loc[
            saidas["Descrição"].map(normalize_text).str.contains("nao contribuinte", na=False), "Valor"
        ].sum()
    )
    creditable_ratio = max(min((base_saidas - non_taxpayer_sales) / base_saidas, 1.0), 0.0) if base_saidas else 0.0

    return DominioSimulationReport(
        empresa=empresa,
        cnpj=cnpj,
        periodo=periodo,
        tributos_atuais=current_values,
        fase_2027=phase_2027,
        fase_2033=phase_2033,
        base_saidas=base_saidas,
        base_entradas_credito=base_entradas,
        aliquota_cbs_2027=parse_br_number(summary.iat[debit_row, 14]) / 100,
        aliquota_ibs_2027=parse_br_number(summary.iat[debit_row, 22]) / 100,
        aliquota_cbs_2033=parse_br_number(summary.iat[debit_row, 50]) / 100,
        aliquota_ibs_2033=parse_br_number(summary.iat[debit_row, 60]) / 100,
        aliquota_credito_cbs_2027=parse_br_number(summary.iat[credit_row, 14]) / 100 if credit_row is not None else 0.0,
        aliquota_credito_ibs_2027=parse_br_number(summary.iat[credit_row, 22]) / 100 if credit_row is not None else 0.0,
        aliquota_credito_cbs_2033=parse_br_number(summary.iat[credit_row, 50]) / 100 if credit_row is not None else 0.0,
        aliquota_credito_ibs_2033=parse_br_number(summary.iat[credit_row, 60]) / 100 if credit_row is not None else 0.0,
        saidas_por_acumulador=saidas,
        entradas_por_acumulador=entradas,
        clientes_por_regime=clientes,
        fornecedores_por_regime=fornecedores,
        vendas_nao_contribuinte=non_taxpayer_sales,
        percentual_operacoes_creditaveis=creditable_ratio,
    )


def _sheet_group_key(sheet_name: str, term: str) -> str:
    """Extrai o identificador que associa abas Resumido/Detalhado da competência."""
    words = normalize_text(sheet_name).split()
    return " ".join(word for word in words if word != normalize_text(term))


def _simulation_sheet_pairs(workbook: pd.ExcelFile) -> list[tuple[str, str]]:
    summaries = [name for name in workbook.sheet_names if "resumido" in normalize_text(name)]
    details = [name for name in workbook.sheet_names if "detalhado" in normalize_text(name)]
    if not summaries or not details:
        raise DominioImportError(
            "O arquivo precisa conter ao menos uma aba Resumido e uma aba Detalhado. "
            f"Abas disponíveis: {', '.join(workbook.sheet_names)}."
        )

    details_by_key: dict[str, list[str]] = {}
    for name in details:
        details_by_key.setdefault(_sheet_group_key(name, "detalhado"), []).append(name)

    pairs: list[tuple[str, str]] = []
    used_details: set[str] = set()
    for index, summary_name in enumerate(summaries):
        key = _sheet_group_key(summary_name, "resumido")
        candidates = [name for name in details_by_key.get(key, []) if name not in used_details]
        if not candidates and len(summaries) == len(details):
            positional = details[index]
            candidates = [positional] if positional not in used_details else []
        if not candidates:
            raise DominioImportError(
                f"Não foi possível associar a aba '{summary_name}' a uma aba Detalhado."
            )
        detail_name = candidates[0]
        used_details.add(detail_name)
        pairs.append((summary_name, detail_name))
    return pairs


def parse_dominio_simulations(content: bytes) -> list[DominioSimulationReport]:
    """Lê todas as competências de um XLS/XLSX, inclusive quando separadas em abas."""
    workbook = _open_legacy_workbook(content)
    reports = [
        _parse_dominio_simulation_frames(
            _read_sheet(workbook, summary_name),
            _read_sheet(workbook, detail_name),
        )
        for summary_name, detail_name in _simulation_sheet_pairs(workbook)
    ]
    if not reports:
        raise DominioImportError("Nenhuma competência foi encontrada no relatório de simulação.")
    return reports


def parse_dominio_simulation(content: bytes) -> DominioSimulationReport:
    """Compatibilidade: devolve a competência mais recente do arquivo."""
    return max(parse_dominio_simulations(content), key=lambda report: report.periodo)


def parse_dominio_monthly(content: bytes) -> MonthlyReport:
    workbook = _open_legacy_workbook(content)
    sheet = workbook.sheet_names[0]
    frame = _read_sheet(workbook, sheet)
    # O Demonstrativo Mensal de empresas sem compras pode omitir a coluna
    # "Entradas R$". Saídas continua obrigatória e Serviços é opcional.
    header_row = _find_row(frame, "ano", "saidas")
    header_cells = [normalize_text(value) for value in frame.iloc[header_row]]

    def monetary_column(term: str) -> int | None:
        return next(
            (
                index
                for index, value in enumerate(header_cells)
                if term in value and "ufir" not in value and "acumulado" not in value
            ),
            None,
        )

    input_column = monetary_column("entradas")
    output_column = monetary_column("saidas")
    service_column = monetary_column("servicos")
    if output_column is None:
        raise DominioImportError("A coluna 'Saídas R$' não foi encontrada no Demonstrativo Mensal.")
    records: list[dict[str, object]] = []
    month_map = {
        "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
        "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    }
    for index in range(header_row + 1, len(frame)):
        month_name = normalize_text(frame.iat[index, 0])
        if month_name.startswith("totais"):
            break
        month_number = next((number for name, number in month_map.items() if month_name.startswith(name)), None)
        if month_number is None:
            continue
        numeric_values = [
            float(value) for value in frame.iloc[index, 1:] if isinstance(value, (int, float)) and not pd.isna(value)
        ]
        year = next((int(value) for value in numeric_values if 2000 <= value <= 2100), None)
        if year is None:
            continue
        records.append(
            {
                "Competência": pd.Timestamp(year=year, month=month_number, day=1),
                "Entradas": parse_br_number(frame.iat[index, input_column]) if input_column is not None else 0.0,
                "Saídas": parse_br_number(frame.iat[index, output_column]),
                "Serviços": parse_br_number(frame.iat[index, service_column]) if service_column is not None else 0.0,
            }
        )
    movements = pd.DataFrame(records)
    if movements.empty:
        raise DominioImportError("Nenhuma competência mensal foi encontrada no Demonstrativo Mensal.")
    return MonthlyReport(
        empresa=str(frame.iat[0, 4]).strip(),
        cnpj=clean_document(frame.iat[3, 4]),
        periodo_inicial=pd.Timestamp(frame.iat[5, 4]),
        periodo_final=pd.Timestamp(frame.iat[5, 13]),
        movimentos=movements,
    )


def _extract_pdf_text(content: bytes) -> str:
    try:
        import fitz

        document = fitz.open(stream=content, filetype="pdf")
        return "\n".join(page.get_text("text") for page in document)
    except Exception as exc:
        raise DominioImportError("Não foi possível extrair o texto do PDF do PGDAS-D.") from exc


def _regex_value(text: str, pattern: str, default: str = "") -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else default


def parse_pgdas(content: bytes) -> PGDASReport:
    text = _extract_pdf_text(content)
    if "PGDAS-D" not in text and "Extrato do Simples Nacional" not in text:
        raise DominioImportError("O PDF não foi reconhecido como extrato do PGDAS-D.")

    empresa = _regex_value(text, r"Nome Empresarial:\s*([^\n]+)")
    cnpj_basic = clean_document(_regex_value(text, r"CNPJ B.sico:\s*([\d.\-/]+)"))
    cnpj_establishment = clean_document(_regex_value(text, r"CNPJ Estabelecimento:\s*([\d.\-/]+)"))
    period = _regex_value(text, r"Per.odo de Apura..o \(PA\):\s*(\d{2}/\d{4})")
    regime = _regex_value(text, r"Regime de Apura..o:\s*([^\n]+)")

    rpa_block = re.search(
        r"Receita Bruta do PA \(RPA\).*?\n([\d.]+,\d{2})\s*\n([\d.]+,\d{2})\s*\n([\d.]+,\d{2})",
        text,
        flags=re.DOTALL,
    )
    rbt12_block = re.search(
        r"\(RBT12\).*?\n([\d.]+,\d{2})\s*\n([\d.]+,\d{2})\s*\n([\d.]+,\d{2})",
        text,
        flags=re.DOTALL,
    )
    rba_block = re.search(
        r"Receita bruta acumulada no ano-calend.rio corrente \(RBA\).*?\n([\d.]+,\d{2}).*?\n([\d.]+,\d{2}).*?\n([\d.]+,\d{2})",
        text,
        flags=re.DOTALL,
    )
    rbaa_block = re.search(
        r"\(RBAA\).*?\n([\d.]+,\d{2}).*?\n([\d.]+,\d{2}).*?\n([\d.]+,\d{2})",
        text,
        flags=re.DOTALL,
    )
    if not rpa_block or not rbt12_block:
        raise DominioImportError("RPA ou RBT12 não foram encontrados no PGDAS-D.")
    rpa = parse_br_number(rpa_block.group(3))
    rbt12 = parse_br_number(rbt12_block.group(3))
    rba = parse_br_number(rba_block.group(3)) if rba_block else 0.0
    rbaa = parse_br_number(rbaa_block.group(3)) if rbaa_block else 0.0

    activity = _regex_value(
        text,
        r"Valor do D.bito por Tributo para a Atividade \(R\$\):\s*(.*?)\s*Receita Bruta Informada:",
    )
    activity = re.sub(r"\s+", " ", activity)
    annex = _regex_value(activity, r"Anexo\s+([IVX]+)")

    tax_pattern = (
        r"IRPJ\s*\nCSLL\s*\nCOFINS\s*\nPIS/Pasep\s*\nINSS/CPP\s*\nICMS\s*\nIPI\s*\nISS\s*\nTotal\s*\n"
        + r"\s*([\d.,]+)\s*\n" * 9
    )
    tax_match = re.search(tax_pattern, text, flags=re.IGNORECASE)
    if not tax_match:
        raise DominioImportError("A composição do DAS não foi encontrada no PGDAS-D.")
    tax_names = ["IRPJ", "CSLL", "COFINS", "PIS/Pasep", "INSS/CPP", "ICMS", "IPI", "ISS", "Total"]
    taxes = {name: parse_br_number(value) for name, value in zip(tax_names, tax_match.groups())}

    history_section = text.split("2.2.1) Mercado Interno", maxsplit=1)[-1].split("2.2.2) Mercado Externo", maxsplit=1)[0]
    history_records = [
        {"Competência": pd.to_datetime(month, format="%m/%Y"), "Receita": parse_br_number(value)}
        for month, value in re.findall(r"(\d{2}/\d{4})\s*\n([\d.]+,\d{2})", history_section)
    ]
    history = pd.DataFrame(history_records)

    return PGDASReport(
        empresa=empresa,
        cnpj_basico=cnpj_basic,
        cnpj_estabelecimento=cnpj_establishment,
        periodo=period,
        regime_apuracao=regime,
        rpa=rpa,
        rbt12=rbt12,
        rba=rba,
        rbaa=rbaa,
        anexo=annex,
        atividade=activity,
        tributos=taxes,
        total_das=taxes["Total"],
        aliquota_efetiva=taxes["Total"] / rpa if rpa else 0.0,
        receitas_anteriores=history,
    )


def cnpjs_are_compatible(dominio_cnpj: str, pgdas: PGDASReport) -> bool:
    dominio = clean_document(dominio_cnpj)
    establishment = clean_document(pgdas.cnpj_estabelecimento)
    if establishment:
        return dominio == establishment
    return bool(pgdas.cnpj_basico) and dominio.startswith(pgdas.cnpj_basico)
