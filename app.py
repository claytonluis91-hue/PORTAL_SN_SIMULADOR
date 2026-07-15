"""Diagnóstico Nascel da Reforma Tributária (IBS/CBS).

Aplicação Streamlit para comparar o Simples Nacional "por dentro" com a
opção híbrida. As premissas editáveis são deliberadamente exibidas na tela:
o projeto é um simulador gerencial e não substitui a apuração fiscal oficial.
"""

from __future__ import annotations

import io
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics_engine import (
    AnalyticsError,
    FutureProjection,
    GEMINI_MODEL_PREFERENCE,
    build_future_projection,
    generate_local_intelligent_report,
    generate_report_with_gemini,
    list_gemini_models,
)
from dashboard_exports import (
    attention_points,
    build_excel_dashboard,
    build_transactional_template,
    create_dashboard_images,
    dashboard_explanations,
    scenario_table,
)
from dominio_importers import (
    DominioImportError,
    DominioSimulationReport,
    MonthlyReport,
    PGDASReport,
    cnpjs_are_compatible,
    parse_dominio_monthly,
    parse_dominio_simulations,
    parse_pgdas,
)
from simples_lc214 import (
    SimplesLC214Error,
    SimplesLC214Simulation,
    simulate_lc214_2027_2028,
    tax_comparison_frame,
)
from nascel_consulting import (
    NASCEL_COLORS,
    NASCEL_LOGO_URL,
    NASCEL_TAGLINE,
    build_nascel_diagnostic,
    decision_matrix_frame,
    legal_timeline_frame,
    official_sources_frame,
)


REFERENCE_IBS_CBS_RATE = 0.265
DEFAULT_DAS_IBS_CBS_SHARE = 0.35
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"


class DataValidationError(ValueError):
    """Erro de entrada que pode ser apresentado diretamente ao usuário."""


@dataclass(frozen=True)
class SimulationInputs:
    faturamento: pd.DataFrame
    entradas: pd.DataFrame
    rbt12: float
    anexo: str
    aliquota_efetiva: float
    aliquota_referencia: float
    fracao_ibs_cbs_das: float
    perda_contratos_percentual: float
    margem_contribuicao: float


@dataclass(frozen=True)
class SimulationResult:
    faturamento_total: float
    faturamento_positivo_classificacao: float
    base_credito_entradas: float
    percentual_b2b: float
    vendas_b2b: float
    cenario_1_das: float
    cenario_1_credito_repassado: float
    cenario_2_das_residual: float
    cenario_2_ibs_cbs_bruto: float
    cenario_2_creditos_disponiveis: float
    cenario_2_creditos_utilizados: float
    cenario_2_saldo_creditos: float
    cenario_2_ibs_cbs_liquido: float
    cenario_2_total: float
    carga_1: float
    carga_2: float
    impacto_hibrido: float
    receita_contratos_em_risco: float
    perda_contratos_estimada: float
    recomendacao: str
    justificativa: str


COLUMN_ALIASES = {
    "faturamento": {
        "Data": {"data", "data_emissao", "dt_emissao", "emissao"},
        "CFOP": {"cfop", "codigo_cfop", "cod_cfop"},
        "CNPJ_CPF_Cliente": {
            "cnpj_cpf_cliente",
            "cnpjcpfcliente",
            "cnpj_cpf",
            "cpf_cnpj",
            "documento_cliente",
            "documento",
        },
        "Valor_Total": {
            "valor_total",
            "valortotal",
            "valor_total_nota",
            "vlr_total",
            "total_nota",
        },
    },
    "entradas": {
        "Data": {"data", "data_emissao", "dt_emissao", "emissao"},
        "CFOP": {"cfop", "codigo_cfop", "cod_cfop"},
        "Valor_Total_Nota": {
            "valor_total_nota",
            "valortotalnota",
            "valor_total",
            "vlr_total_nota",
            "total_nota",
        },
        "Valor_Base_Credito": {
            "valor_base_credito",
            "valorbasecredito",
            "base_credito",
            "vlr_base_credito",
            "base_ibs_cbs",
        },
    },
    "parametros": {
        "RBT12": {"rbt12", "receita_bruta_12_meses", "receita_12_meses"},
        "Anexo": {"anexo", "anexo_sn"},
        "Aliquota_Efetiva_Atual": {
            "aliquota_efetiva_atual",
            "aliquotaefetivaatual",
            "aliquota_efetiva",
            "aliquota_atual",
        },
    },
}


def normalize_column_name(value: object) -> str:
    """Normaliza cabeçalhos do Domínio sem depender de grafia/acentuação exata."""
    text = unicodedata.normalize("NFKD", str(value).strip())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def read_uploaded_table(file_name: str, content: bytes) -> pd.DataFrame:
    """Lê CSV/XLSX testando codificações e separadores comuns no Brasil."""
    suffix = file_name.lower().rsplit(".", maxsplit=1)[-1]
    if suffix in {"xlsx", "xls"}:
        errors: list[str] = []
        engines = ("calamine", "openpyxl") if suffix == "xlsx" else ("calamine", "xlrd")
        for engine in engines:
            try:
                return pd.read_excel(io.BytesIO(content), dtype=object, engine=engine)
            except Exception as exc:
                errors.append(f"{engine}: {exc}")
        raise DataValidationError(f"Não foi possível abrir {file_name}: {' | '.join(errors)}")

    if suffix != "csv":
        raise DataValidationError(f"Formato não suportado em {file_name}. Use CSV ou XLSX.")

    errors: list[str] = []
    candidates: list[tuple[int, int, pd.DataFrame]] = []
    known_columns = {
        normalize_column_name(alias)
        for dataset_aliases in COLUMN_ALIASES.values()
        for canonical, aliases in dataset_aliases.items()
        for alias in {canonical, *aliases}
    }
    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        for separator in (None, ";", ",", "\t", "|"):
            try:
                frame = pd.read_csv(
                    io.StringIO(text),
                    sep=separator,
                    engine="python",
                    dtype=object,
                    keep_default_na=True,
                )
                if frame.shape[1] > 1:
                    normalized = {normalize_column_name(column) for column in frame.columns}
                    score = len(normalized & known_columns)
                    candidates.append((score, -frame.shape[1], frame))
            except Exception as exc:
                errors.append(str(exc))
        if candidates and max(item[0] for item in candidates) >= 3:
            break
    if candidates:
        return max(candidates, key=lambda item: (item[0], item[1]))[2]
    detail = errors[-1] if errors else "codificação ou separador não reconhecido"
    raise DataValidationError(f"Não foi possível interpretar {file_name}: {detail}")


def rename_and_validate_columns(frame: pd.DataFrame, dataset: str) -> pd.DataFrame:
    if dataset not in COLUMN_ALIASES:
        raise DataValidationError(f"Conjunto de dados desconhecido: {dataset}.")
    aliases = COLUMN_ALIASES[dataset]
    normalized_to_original: dict[str, list[object]] = {}
    for column in frame.columns:
        normalized_to_original.setdefault(normalize_column_name(column), []).append(column)
    rename_map: dict[object, str] = {}
    missing: list[str] = []

    for canonical, accepted in aliases.items():
        possible = list(dict.fromkeys([normalize_column_name(canonical), *sorted(accepted)]))
        matches = [
            original
            for item in possible
            for original in normalized_to_original.get(item, [])
        ]
        if not matches:
            missing.append(canonical)
        elif len(matches) > 1:
            raise DataValidationError(
                f"Arquivo de {dataset}: mais de uma coluna pode representar {canonical}: "
                f"{', '.join(map(str, matches))}. Mantenha somente uma delas."
            )
        else:
            rename_map[matches[0]] = canonical

    if missing:
        available = ", ".join(map(str, frame.columns))
        raise DataValidationError(
            f"Arquivo de {dataset}: colunas ausentes: {', '.join(missing)}. "
            f"Colunas encontradas: {available or 'nenhuma'}."
        )
    return frame.rename(columns=rename_map).copy()


def parse_brazilian_number(value: object) -> float:
    """Converte números, moedas e percentuais em formatos BR e internacional."""
    if pd.isna(value) or str(value).strip() == "":
        return float("nan")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    text = str(value).strip().replace("R$", "").replace("%", "").replace(" ", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = re.sub(r"[^0-9,.-]", "", text)
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    elif text.count(".") == 1 and len(text.rsplit(".", maxsplit=1)[1]) == 3:
        # Em exportações brasileiras, um único ponto seguido de três dígitos
        # normalmente é separador de milhar ("1.234").
        text = text.replace(".", "")
    try:
        number = float(text)
        return -number if negative else number
    except ValueError:
        return float("nan")


def parse_rate(value: object) -> float:
    """Converte alíquota decimal ou percentual sem confundir 0,10% com 10%."""
    number = parse_brazilian_number(value)
    if pd.isna(number):
        return float("nan")
    if isinstance(value, str) and "%" in value:
        return number / 100
    return number / 100 if number > 1 else number


def clean_document(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    # Corrige documentos importados pelo Excel como 123...0.
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return re.sub(r"\D", "", text)


def classify_customer(document: object) -> str:
    """Classifica CPF/PF e CNPJ/PJ por quantidade de dígitos válidos."""
    digits = clean_document(document)
    if len(digits) <= 11 and digits:
        return "PF"
    if len(digits) > 11:
        return "PJ"
    return "Não identificado"


def prepare_transaction_data(
    raw_frame: pd.DataFrame, dataset: str
) -> tuple[pd.DataFrame, list[str]]:
    frame = rename_and_validate_columns(raw_frame, dataset)
    warnings: list[str] = []
    initial_rows = len(frame)
    frame = frame.dropna(how="all").copy()
    if len(frame) < initial_rows:
        warnings.append(f"{initial_rows - len(frame)} linha(s) totalmente vazia(s) foram removidas.")

    frame["Data"] = pd.to_datetime(frame["Data"], errors="coerce", dayfirst=True, format="mixed")
    invalid_dates = int(frame["Data"].isna().sum())
    if invalid_dates:
        warnings.append(f"{invalid_dates} linha(s) possuem data ausente ou inválida.")

    frame["CFOP"] = frame["CFOP"].astype("string").str.replace(r"\.0$", "", regex=True).str.strip()
    invalid_cfop = int(frame["CFOP"].isna().sum() + frame["CFOP"].eq("").sum())
    if invalid_cfop:
        warnings.append(f"{invalid_cfop} linha(s) estão sem CFOP.")

    numeric_columns = (
        ["Valor_Total"]
        if dataset == "faturamento"
        else ["Valor_Total_Nota", "Valor_Base_Credito"]
    )
    for column in numeric_columns:
        frame[column] = frame[column].map(parse_brazilian_number)
        invalid = int(frame[column].isna().sum())
        if invalid:
            warnings.append(f"{invalid} valor(es) inválido(s) em {column} foram tratados como zero.")
        frame[column] = frame[column].fillna(0.0)

    value_column = "Valor_Total" if dataset == "faturamento" else "Valor_Total_Nota"
    negative_values = int(frame[value_column].lt(0).sum())
    if negative_values:
        warnings.append(
            f"{negative_values} lançamento(s) negativo(s) foram mantidos como devoluções/ajustes."
        )

    duplicated = int(frame.duplicated().sum())
    if duplicated:
        warnings.append(
            f"Foram encontradas {duplicated} linha(s) duplicada(s); elas foram mantidas para não alterar a escrituração."
        )

    if dataset == "faturamento":
        frame["Documento_Limpo"] = frame["CNPJ_CPF_Cliente"].map(clean_document)
        frame["Tipo_Cliente"] = frame["CNPJ_CPF_Cliente"].map(classify_customer)
        unidentified = int(frame["Tipo_Cliente"].eq("Não identificado").sum())
        if unidentified:
            warnings.append(f"{unidentified} venda(s) não possuem CPF/CNPJ identificável.")

    return frame, warnings


def prepare_parameters(raw_frame: pd.DataFrame) -> tuple[float, str, float, list[str]]:
    frame = rename_and_validate_columns(raw_frame.dropna(how="all"), "parametros")
    if frame.empty:
        raise DataValidationError("O arquivo de parâmetros não contém registros.")

    warnings: list[str] = []
    if len(frame) > 1:
        warnings.append("O arquivo de parâmetros possui mais de uma linha; foi usada a primeira linha válida.")
    frame["RBT12"] = frame["RBT12"].map(parse_brazilian_number)
    frame["Aliquota_Efetiva_Atual"] = frame["Aliquota_Efetiva_Atual"].map(parse_rate)
    valid = frame.dropna(subset=["RBT12", "Aliquota_Efetiva_Atual"])
    if valid.empty:
        raise DataValidationError("RBT12 e alíquota efetiva precisam conter valores numéricos válidos.")

    row = valid.iloc[0]
    rbt12 = float(row["RBT12"])
    rate = float(row["Aliquota_Efetiva_Atual"])
    if rbt12 < 0 or not 0 <= rate <= 1:
        raise DataValidationError("RBT12 deve ser positivo e a alíquota efetiva deve estar entre 0% e 100%.")
    return rbt12, str(row["Anexo"]).strip(), rate, warnings


def safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def run_simulation(inputs: SimulationInputs) -> SimulationResult:
    rates = {
        "alíquota efetiva": inputs.aliquota_efetiva,
        "alíquota de referência": inputs.aliquota_referencia,
        "fração de IBS/CBS no DAS": inputs.fracao_ibs_cbs_das,
        "perda de contratos": inputs.perda_contratos_percentual,
        "margem de contribuição": inputs.margem_contribuicao,
    }
    invalid_rates = [name for name, value in rates.items() if not 0 <= value <= 1]
    if invalid_rates:
        raise DataValidationError(
            f"Percentual fora do intervalo de 0% a 100%: {', '.join(invalid_rates)}."
        )

    revenue = float(inputs.faturamento["Valor_Total"].sum())
    if revenue <= 0:
        raise DataValidationError("O faturamento total precisa ser maior que zero para executar a simulação.")

    # A dependência comercial usa apenas vendas positivas para que devoluções
    # não produzam percentuais negativos ou acima de 100%. A carga tributária
    # continua usando o faturamento líquido informado.
    positive_sales = inputs.faturamento["Valor_Total"].clip(lower=0)
    classification_base = float(positive_sales.sum())
    b2b_mask = inputs.faturamento["Tipo_Cliente"].eq("PJ")
    b2b_sales = float(positive_sales.loc[b2b_mask].sum())
    b2b_ratio = safe_ratio(b2b_sales, classification_base)
    input_base = max(float(inputs.entradas["Valor_Base_Credito"].sum()), 0.0)

    ibs_cbs_das_rate = inputs.aliquota_efetiva * inputs.fracao_ibs_cbs_das
    scenario_1_das = revenue * inputs.aliquota_efetiva
    scenario_1_credit = b2b_sales * ibs_cbs_das_rate

    residual_das_rate = max(inputs.aliquota_efetiva - ibs_cbs_das_rate, 0.0)
    scenario_2_das = revenue * residual_das_rate
    scenario_2_gross = revenue * inputs.aliquota_referencia
    scenario_2_available_credits = input_base * inputs.aliquota_referencia
    scenario_2_used_credits = min(scenario_2_available_credits, scenario_2_gross)
    scenario_2_credit_balance = max(scenario_2_available_credits - scenario_2_used_credits, 0.0)
    scenario_2_net = max(scenario_2_gross - scenario_2_used_credits, 0.0)
    scenario_2_total = scenario_2_das + scenario_2_net

    impact = scenario_2_total - scenario_1_das
    at_risk_revenue = classification_base * inputs.perda_contratos_percentual
    contract_loss = at_risk_revenue * inputs.margem_contribuicao
    recommend_hybrid = b2b_ratio > 0.60 and impact < contract_loss
    recommendation = "Híbrido" if recommend_hybrid else "Por Dentro"
    if recommend_hybrid:
        reason = (
            "A participação B2B supera 60% e o impacto incremental do regime híbrido "
            "é inferior ao prejuízo econômico estimado dos contratos em risco."
        )
    elif b2b_ratio <= 0.60:
        reason = "A participação B2B não supera o limite de 60% definido para a recomendação do híbrido."
    else:
        reason = (
            "O impacto incremental do híbrido é igual ou superior ao prejuízo econômico estimado "
            "dos contratos em risco; "
            "a permanência por dentro apresenta menor risco na premissa informada."
        )

    return SimulationResult(
        faturamento_total=revenue,
        faturamento_positivo_classificacao=classification_base,
        base_credito_entradas=input_base,
        percentual_b2b=b2b_ratio,
        vendas_b2b=b2b_sales,
        cenario_1_das=scenario_1_das,
        cenario_1_credito_repassado=scenario_1_credit,
        cenario_2_das_residual=scenario_2_das,
        cenario_2_ibs_cbs_bruto=scenario_2_gross,
        cenario_2_creditos_disponiveis=scenario_2_available_credits,
        cenario_2_creditos_utilizados=scenario_2_used_credits,
        cenario_2_saldo_creditos=scenario_2_credit_balance,
        cenario_2_ibs_cbs_liquido=scenario_2_net,
        cenario_2_total=scenario_2_total,
        carga_1=safe_ratio(scenario_1_das, revenue),
        carga_2=safe_ratio(scenario_2_total, revenue),
        impacto_hibrido=impact,
        receita_contratos_em_risco=at_risk_revenue,
        perda_contratos_estimada=contract_loss,
        recomendacao=recommendation,
        justificativa=reason,
    )


def brl(value: float) -> str:
    formatted = f"{value:,.2f}"
    return "R$ " + formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(value: float) -> str:
    return f"{value * 100:.2f}%".replace(".", ",")


def build_pdf(result: SimulationResult, inputs: SimulationInputs) -> bytes:
    """Cria um relatório executivo em PDF, pronto para download."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise DataValidationError("A biblioteca reportlab não está instalada. Execute: pip install reportlab") from exc

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Diagnóstico Nascel - Reforma Tributária IBS/CBS",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CenteredTitle", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor(NASCEL_COLORS["navy"])))
    story: list[object] = [
        Paragraph("GRUPO NASCEL | NASCEL CONTABILIDADE", styles["CenteredTitle"]),
        Paragraph("Diagnóstico da Reforma Tributária — Simples Nacional", styles["Heading2"]),
        Paragraph(NASCEL_TAGLINE, styles["Italic"]),
        Paragraph(f"Relatório gerado em {datetime.now():%d/%m/%Y às %H:%M}", styles["Normal"]),
        Spacer(1, 8 * mm),
    ]
    summary = [
        ["Indicador", "Por Dentro", "Híbrido"],
        ["Carga tributária", pct(result.carga_1), pct(result.carga_2)],
        ["Custo tributário", brl(result.cenario_1_das), brl(result.cenario_2_total)],
        ["Crédito ao comprador B2B", brl(result.cenario_1_credito_repassado), brl(result.vendas_b2b * inputs.aliquota_referencia)],
    ]
    table = Table(summary, colWidths=[65 * mm, 48 * mm, 48 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NASCEL_COLORS["navy"])),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor(NASCEL_COLORS["cream"])),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("PADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([table, Spacer(1, 8 * mm)])
    details = [
        ["Faturamento analisado", brl(result.faturamento_total)],
        ["Vendas para PJ", f"{pct(result.percentual_b2b)} ({brl(result.vendas_b2b)})"],
        ["Base informada para créditos", brl(result.base_credito_entradas)],
        ["Créditos disponíveis estimados", brl(result.cenario_2_creditos_disponiveis)],
        ["Créditos utilizados no período", brl(result.cenario_2_creditos_utilizados)],
        ["Saldo de créditos estimado", brl(result.cenario_2_saldo_creditos)],
        ["RBT12", brl(inputs.rbt12)],
        ["Anexo", inputs.anexo],
        ["Alíquota efetiva atual", pct(inputs.aliquota_efetiva)],
        ["Alíquota de referência IBS/CBS", pct(inputs.aliquota_referencia)],
        ["Participação IBS/CBS na alíquota do DAS", pct(inputs.fracao_ibs_cbs_das)],
        ["Receita de contratos em risco", brl(result.receita_contratos_em_risco)],
        ["Margem de contribuição", pct(inputs.margem_contribuicao)],
        ["Prejuízo econômico simulado", brl(result.perda_contratos_estimada)],
    ]
    detail_table = Table(details, colWidths=[80 * mm, 81 * mm])
    detail_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor(NASCEL_COLORS["light_gold"])),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([
        Paragraph(f"Recomendação: {result.recomendacao}", styles["Heading2"]),
        Paragraph(result.justificativa, styles["BodyText"]),
        Spacer(1, 6 * mm),
        Paragraph("Como interpretar", styles["Heading2"]),
        Paragraph(
            "Por Dentro prioriza simplicidade e tende a atender melhor operações com consumidor final. "
            "O Híbrido pode fortalecer a cadeia B2B, mas exige validar créditos das entradas, documentos, "
            "preço, margem e capital de giro. Compare o efeito líquido, não apenas a alíquota nominal.",
            styles["BodyText"],
        ),
        Spacer(1, 5 * mm),
        Paragraph("Premissas e dados consolidados", styles["Heading2"]),
        detail_table,
        Spacer(1, 6 * mm),
        Paragraph(
            "Aviso: simulação gerencial baseada nas premissas informadas. Valide enquadramento, "
            "percentuais, direito aos créditos e regras de transição com a legislação vigente antes "
            "de qualquer decisão. A alíquota combinada informada representa uma premissa estrutural, "
            "não uma projeção automática das alíquotas anuais de transição.",
            styles["Italic"],
        ),
    ])
    document.build(story)
    return buffer.getvalue()


def comparison_chart(result: SimulationResult) -> go.Figure:
    figure = go.Figure()
    figure.add_bar(
        name="Custo tributário",
        x=["Por Dentro", "Híbrido"],
        y=[result.cenario_1_das, result.cenario_2_total],
        marker_color=[NASCEL_COLORS["navy"], NASCEL_COLORS["gold"]],
        text=[brl(result.cenario_1_das), brl(result.cenario_2_total)],
        textposition="outside",
    )
    figure.update_layout(
        title="Custo tributário estimado",
        yaxis_title="Valor (R$)",
        showlegend=False,
        margin=dict(l=20, r=20, t=70, b=20),
        height=390,
    )
    return figure


def credit_chart(result: SimulationResult, reference_rate: float) -> go.Figure:
    hybrid_credit = result.vendas_b2b * reference_rate
    figure = go.Figure(go.Bar(
        x=["Por Dentro", "Híbrido"],
        y=[result.cenario_1_credito_repassado, hybrid_credit],
        marker_color=[NASCEL_COLORS["navy"], NASCEL_COLORS["gold"]],
        text=[brl(result.cenario_1_credito_repassado), brl(hybrid_credit)],
        textposition="outside",
    ))
    figure.update_layout(
        title="Crédito potencial repassado aos compradores PJ",
        yaxis_title="Valor (R$)",
        margin=dict(l=20, r=20, t=70, b=20),
        height=390,
    )
    return figure


def show_warnings(groups: Iterable[tuple[str, list[str]]]) -> None:
    messages = [f"**{name}:** {message}" for name, warnings in groups for message in warnings]
    if messages:
        with st.expander(f"Qualidade dos dados — {len(messages)} aviso(s)", expanded=False):
            for message in messages:
                st.warning(message)


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {--nascel-navy:#16163F; --nascel-gold:#F9AF44; --nascel-cream:#F8F3EC; --nascel-border:#E6E2DC; --nascel-muted:#626277;}
        html, body, [class*="css"] {font-family: "Montserrat", "Segoe UI", Arial, sans-serif;}
        .block-container {max-width: 1180px; padding-top: 1.5rem; padding-bottom: 3rem;}
        [data-testid="stMetric"] {background:#FFFFFF; border:1px solid var(--nascel-border); border-top:3px solid var(--nascel-gold); padding:1rem 1.05rem; border-radius:12px; box-shadow:0 3px 12px rgba(22,22,63,.05); min-height:132px;}
        [data-testid="stMetricLabel"] {color:var(--nascel-muted); font-weight:650; line-height:1.3; white-space:normal;}
        [data-testid="stMetricValue"] {color:var(--nascel-navy); font-variant-numeric:tabular-nums; letter-spacing:-.025em;}
        .nascel-brand {display:flex; align-items:center; justify-content:space-between; gap:2rem; padding:1.25rem 1.5rem; background:var(--nascel-navy); border-bottom:5px solid var(--nascel-gold); border-radius:12px; margin-bottom:1.2rem;}
        .nascel-brand img {width:190px; max-width:38%; height:auto;}
        .nascel-brand-copy {color:#FFFFFF; text-align:right; font-size:.95rem; line-height:1.45;}
        .nascel-brand-copy strong {display:block; color:var(--nascel-gold); text-transform:uppercase; letter-spacing:.08em; font-size:.76rem;}
        .recommendation {padding:1.2rem 1.4rem; border-radius:12px; background:#FFF8EA; border:1px solid #F2D59F; border-left:5px solid var(--nascel-gold); color:var(--nascel-navy);}
        .subtitle {color: #5B5B6E; margin-top: -0.7rem;}
        h1, h2, h3 {color:var(--nascel-navy); letter-spacing:-.025em;}
        h2 {margin-top:2rem; padding-top:.25rem;}
        div[data-testid="stExpander"] {border-color:var(--nascel-border); border-radius:10px;}
        [data-testid="stDataFrame"] {border:1px solid var(--nascel-border); border-radius:10px; overflow:hidden;}
        @media (max-width: 700px) {.nascel-brand {align-items:flex-start; flex-direction:column;} .nascel-brand-copy {text-align:left;} .nascel-brand img {max-width:70%;}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def format_cnpj(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return value


def get_streamlit_secret(name: str, default: str = "") -> str:
    """Lê secret sem falhar quando o arquivo ainda não foi configurado."""
    try:
        return str(st.secrets.get(name, os.getenv(name, default)))
    except Exception:
        return os.getenv(name, default)


def get_gemini_api_key() -> tuple[str, str]:
    """Obtém a chave sem exibi-la; GOOGLE_API_KEY tem a precedência oficial."""
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        value = get_streamlit_secret(name)
        if value:
            return value, name
    return "", ""


def render_dominio_results(
    report: DominioSimulationReport,
    monthly: MonthlyReport,
    pgdas_report: PGDASReport | None,
    reports: list[DominioSimulationReport],
    projection: FutureProjection,
    intelligent_report: str,
    lc214_simulation: SimplesLC214Simulation,
    lc214_comparison: pd.DataFrame,
) -> None:
    st.success(
        f"Relatórios reconhecidos: {report.empresa} · CNPJ {format_cnpj(report.cnpj)} · "
        f"competência {report.periodo:%m/%Y}."
    )
    if report.cnpj != monthly.cnpj:
        st.error(
            f"Os arquivos do Domínio pertencem a CNPJs diferentes: simulação {format_cnpj(report.cnpj)} "
            f"e demonstrativo {format_cnpj(monthly.cnpj)}. A consolidação foi interrompida."
        )
        return
    if pgdas_report and not cnpjs_are_compatible(report.cnpj, pgdas_report):
        st.error(
            f"O PGDAS pertence ao CNPJ {format_cnpj(pgdas_report.cnpj_estabelecimento or pgdas_report.cnpj_basico)}, "
            f"diferente do CNPJ {format_cnpj(report.cnpj)} do Domínio. Ele será exibido apenas como exemplo e "
            "não será usado nos cálculos da empresa."
        )

    period_rows = monthly.movimentos[
        monthly.movimentos["Competência"].dt.to_period("M") == report.periodo.to_period("M")
    ]
    monthly_inputs = float(period_rows["Entradas"].sum()) if not period_rows.empty else 0.0
    reconciliation = monthly_inputs - report.base_entradas_credito
    purchase_creditable_ratio = (
        min(max(report.base_entradas_credito / monthly_inputs, 0.0), 1.0)
        if monthly_inputs > 0
        else 0.0
    )

    explanations = dashboard_explanations(
        report, monthly, pgdas_report, projection, lc214_simulation
    )
    diagnostic = build_nascel_diagnostic(
        report, monthly, pgdas_report, reports, lc214_simulation
    )
    decision_matrix = decision_matrix_frame(report, lc214_simulation)
    legal_timeline = legal_timeline_frame()
    official_sources = official_sources_frame()
    st.info(
        "Leitura sugerida: 1) confira a base importada; 2) compare 2026 com 2027 na mesma base; "
        "3) avalie os créditos estimados das compras; 4) valide os pontos de atenção antes da decisão."
    )
    with st.expander("Como ler este dashboard e o que entra em cada cálculo", expanded=True):
        st.markdown(
            "**Valores importados** vêm dos relatórios Domínio/PGDAS. "
            "**Valores calculados** aplicam essas bases às alíquotas dos cenários. "
            "**Premissas** são estimativas ajustáveis e precisam ser confirmadas."
        )
        st.dataframe(explanations, hide_index=True, width="stretch")

    st.subheader("1. Diagnóstico executivo Nascel")
    diagnostic_columns = st.columns([1, 2, 3])
    diagnostic_columns[0].metric(
        "Índice de confiança dos dados",
        f"{diagnostic.score}/100",
        help="Pontuação gerencial baseada em histórico, PGDAS, conciliação, clientes e fornecedores. Não mede risco jurídico.",
    )
    diagnostic_columns[1].metric(
        "Situação para decisão",
        diagnostic.status,
        help="Indica se os dados permitem avançar para uma decisão assistida ou se ainda há premissas a revisar.",
    )
    diagnostic_columns[2].metric(
        "Direção inicial do estudo",
        "Fora do DAS" if "fora" in diagnostic.recommendation.lower() else "Dentro do DAS" if "dentro" in diagnostic.recommendation.lower() else "Aguardar validações",
        help="Direção preliminar, nunca uma escolha automática. A opção depende do impacto líquido e das validações listadas.",
    )
    st.markdown(
        f'<div class="recommendation"><h3>Leitura consultiva</h3><p><strong>{diagnostic.recommendation}</strong> '
        f'{diagnostic.rationale}</p></div>',
        unsafe_allow_html=True,
    )
    with st.expander("Checklist que forma o índice de confiança"):
        st.dataframe(diagnostic.checklist, hide_index=True, width="stretch")

    st.subheader("2. Base analisada e resultado consolidado")
    base_metrics = st.columns(3)
    base_metrics[0].metric(
        "Saídas analisadas", brl(report.base_saidas),
        help="Receita da competência usada como base para medir a carga tributária. Não representa lucro ou caixa.",
    )
    base_metrics[1].metric(
        "Base de entradas", brl(report.base_entradas_credito),
        help="Aquisições consideradas potencialmente aptas a gerar crédito. Exige validação fiscal por documento.",
    )
    base_metrics[2].metric(
        "Compras potencialmente creditáveis", pct(purchase_creditable_ratio),
        help="Base de crédito das entradas dividida pelas compras da mesma competência. Exige validação por documento e item.",
    )
    impact_metrics = st.columns(2)
    impact_metrics[0].metric(
        "Impacto Híbrido 2027", brl(report.fase_2027["diferenca"]),
        delta=pct(report.fase_2027["diferenca_percentual"]), delta_color="inverse",
        help="Diferença entre o total híbrido de 2027 e a carga atual, segundo o relatório Domínio.",
    )
    impact_metrics[1].metric(
        "Impacto Híbrido 2033", brl(report.fase_2033["diferenca"]),
        delta=pct(report.fase_2033["diferenca_percentual"]), delta_color="inverse",
        help="Diferença estrutural estimada para 2033. Depende das alíquotas e regras futuras informadas no arquivo.",
    )

    st.subheader("3. Comparação dos cenários")
    scenarios = scenario_table(report, lc214_simulation)
    chart_columns = st.columns(2)
    with chart_columns[0]:
        figure = go.Figure(go.Bar(
            x=scenarios["Cenário"], y=scenarios["Carga Tributária"],
            marker_color=[NASCEL_COLORS["slate"], NASCEL_COLORS["navy"], NASCEL_COLORS["gold"], NASCEL_COLORS["green"]],
            text=[brl(value) for value in scenarios["Carga Tributária"]], textposition="outside",
        ))
        figure.update_layout(title="Carga tributária por cenário", yaxis_title="R$", height=430, margin=dict(l=20, r=20, t=60, b=90))
        st.plotly_chart(figure, width="stretch")
        st.caption("Compara quanto a empresa desembolsaria em tributos em cada cenário para a mesma base de saídas.")
    with chart_columns[1]:
        figure = go.Figure(go.Bar(
            x=scenarios["Cenário"], y=scenarios["Crédito Estimado das Compras"],
            marker_color=NASCEL_COLORS["gold"],
            text=[brl(value) for value in scenarios["Crédito Estimado das Compras"]], textposition="outside",
        ))
        figure.update_layout(title="Crédito estimado sobre as compras", yaxis_title="R$", height=430, margin=dict(l=20, r=20, t=60, b=90))
        st.plotly_chart(figure, width="stretch")
        st.caption(
            "Estimativa do crédito de CBS/IBS que a própria empresa poderá tomar "
            "sobre a base atual de compras creditáveis. No Por Dentro, esse valor é zero."
        )

    scenarios_display = scenarios.copy()
    scenarios_display["Carga Efetiva"] = scenarios_display["Carga Efetiva"] * 100
    st.dataframe(
        scenarios_display,
        hide_index=True,
        width="stretch",
        column_config={
            "Carga Tributária": st.column_config.NumberColumn(format="R$ %.2f"),
            "Carga Efetiva": st.column_config.NumberColumn(format="%.2f%%"),
            "Crédito Estimado das Compras": st.column_config.NumberColumn(format="R$ %.2f"),
            "Variação vs. Atual": st.column_config.NumberColumn(format="R$ %.2f"),
        },
    )

    st.subheader("4. Decisão tributária 2027/2028 — dentro ou fora do DAS")
    current_total = report.tributos_atuais["Total"]
    current_rate = current_total / report.base_saidas if report.base_saidas else 0.0
    hybrid_2027_total = report.fase_2027["total"]
    lc_metrics = st.columns(4)
    lc_metrics[0].metric("Anexo / faixa", f"{lc214_simulation.annex} / {lc214_simulation.bracket}ª")
    lc_metrics[1].metric("Alíquota efetiva 2026 e 2027", pct(current_rate))
    lc_metrics[2].metric("2027 Por Dentro · DAS", brl(current_total), delta="R$ 0,00 vs. 2026")
    lc_metrics[3].metric(
        "2027 Híbrido · DAS + CBS/IBS",
        brl(hybrid_2027_total),
        delta=brl(hybrid_2027_total - current_total),
        delta_color="inverse",
    )
    st.success(
        "2026 e 2027 Por Dentro: mesma alíquota efetiva e mesmo DAS quando receita, Anexo e segregações "
        "permanecem iguais. Em 2027 muda a repartição interna da guia, não a carga total desta comparação."
    )
    st.dataframe(
        lc214_comparison,
        hide_index=True,
        width="stretch",
        column_config={
            "Atual (PGDAS/Domínio)": st.column_config.NumberColumn(format="R$ %.2f"),
            "2027/2028 Por Dentro (DAS)": st.column_config.NumberColumn(format="R$ %.2f"),
            "2027 Por Fora (DAS + regular)": st.column_config.NumberColumn(format="R$ %.2f"),
        },
    )
    st.caption(
        "Por Dentro: CBS e IBS permanecem na guia do Simples, sem diferença de carga vs. 2026 nesta mesma base. "
        "Por Fora: o DAS fica residual e CBS/IBS são recolhidos pelo regime regular. No Anexo II, o IPI permanece "
        "na partilha oficial de 2027/2028; o sistema não zera o IPI nem troca o Anexo automaticamente."
    )
    st.markdown("#### Matriz de decisão Nascel")
    st.dataframe(
        decision_matrix,
        hide_index=True,
        width="stretch",
        column_config={
            "Crédito estimado das compras": st.column_config.NumberColumn(format="R$ %.2f"),
            "Desembolso estimado": st.column_config.NumberColumn(format="R$ %.2f"),
        },
    )
    with st.expander("Cronograma legal e ações de preparação"):
        st.dataframe(legal_timeline, hide_index=True, width="stretch")
        st.caption(
            "A LC 214/2025 recebeu alterações posteriores, inclusive pela LC 227/2026. "
            "Prazos e regulamentações devem ser confirmados antes de cada opção semestral."
        )
        st.markdown("**Fontes oficiais para conferência**")
        for source in official_sources.itertuples(index=False):
            st.markdown(f"- [{source.Documento}]({source.URL}) — {source.Escopo}")

    st.subheader("5. Projeção anual simplificada — 2027 e 2033")
    st.caption(
        f"2027 considera janeiro a dezembro, a média dos últimos {projection.meses_media} meses "
        f"e crescimento {'calculado pelo histórico' if projection.modo_crescimento == 'average' else 'informado pelo usuário'} "
        f"de {pct(projection.crescimento_anual)} ao ano. Para facilitar a comparação, 2033 usa "
        "a mesma receita, a mesma base de compras e a mesma alíquota efetiva do DAS Normal "
        "projetadas para 2027; mudam apenas as premissas tributárias do Híbrido."
    )
    totals = projection.totais
    annual = projection.resumo_anual
    annual_metrics = st.columns(4)
    annual_metrics[0].metric("Receita anual projetada", brl(totals["receita"]))
    annual_metrics[1].metric("Compras anuais projetadas", brl(totals["entradas"]))
    annual_metrics[2].metric(
        "Base creditável das compras",
        brl(float(annual.iloc[0]["Base Creditável das Compras"])),
        help="Mantém a proporção conciliada entre a base de crédito do Domínio e as entradas da mesma competência.",
    )
    annual_metrics[3].metric(
        "Compras potencialmente creditáveis",
        pct(projection.percentual_entradas_creditaveis),
    )

    year_2027 = annual.iloc[0]
    year_2033 = annual.iloc[1]
    scenario_metrics = st.columns(4)
    scenario_metrics[0].metric(
        f"2027 · DAS Normal · {pct(float(year_2027['DAS Normal · Alíquota Efetiva']))}",
        brl(float(year_2027["DAS Normal · Valor"])),
    )
    scenario_metrics[1].metric(
        f"2027 · Híbrido · {pct(float(year_2027['Híbrido · Alíquota Efetiva']))}",
        brl(float(year_2027["Híbrido · Total a Pagar"])),
        delta=brl(float(year_2027["Diferença vs. DAS Normal"])),
        delta_color="inverse",
    )
    scenario_metrics[2].metric(
        f"2033 · DAS Normal · {pct(float(year_2033['DAS Normal · Alíquota Efetiva']))}",
        brl(float(year_2033["DAS Normal · Valor"])),
    )
    scenario_metrics[3].metric(
        f"2033 · Híbrido · {pct(float(year_2033['Híbrido · Alíquota Efetiva']))}",
        brl(float(year_2033["Híbrido · Total a Pagar"])),
        delta=brl(float(year_2033["Diferença vs. DAS Normal"])),
        delta_color="inverse",
    )

    projection_chart = go.Figure()
    projection_chart.add_bar(
        x=annual["Período"],
        y=annual["DAS Normal · Valor"],
        name="DAS Normal",
        marker_color=NASCEL_COLORS["navy"],
        text=[
            f"{brl(value)} · {pct(rate)}"
            for value, rate in zip(
                annual["DAS Normal · Valor"],
                annual["DAS Normal · Alíquota Efetiva"],
            )
        ],
        textposition="outside",
    )
    projection_chart.add_bar(
        x=annual["Período"],
        y=annual["Híbrido · Total a Pagar"],
        name="Híbrido",
        marker_color=NASCEL_COLORS["gold"],
        text=[
            f"{brl(value)} · {pct(rate)}"
            for value, rate in zip(
                annual["Híbrido · Total a Pagar"],
                annual["Híbrido · Alíquota Efetiva"],
            )
        ],
        textposition="outside",
    )
    projection_chart.update_layout(
        barmode="group",
        yaxis_title="Total anual estimado (R$)",
        height=430,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(projection_chart, width="stretch")
    st.caption(
        "No DAS Normal, CBS e IBS permanecem na guia e não geram crédito das compras para a empresa. "
        "No Híbrido, o quadro mostra DAS residual, CBS e IBS após o abatimento do crédito estimado das compras."
    )
    annual_display = annual.copy()
    for rate_column in (
        "DAS Normal · Alíquota Efetiva",
        "Híbrido · Alíquota Efetiva",
    ):
        annual_display[rate_column] = annual_display[rate_column] * 100
    st.dataframe(
        annual_display,
        hide_index=True,
        width="stretch",
        column_config={
            "DAS Normal · Alíquota Efetiva": st.column_config.NumberColumn(format="%.2f%%"),
            "Híbrido · Alíquota Efetiva": st.column_config.NumberColumn(format="%.2f%%"),
            **{
                column: st.column_config.NumberColumn(format="R$ %.2f")
                for column in annual.columns
                if column != "Período" and "Alíquota" not in column
            },
        },
    )
    with st.expander("Ver períodos importados e premissas da projeção"):
        st.caption(
            f"Média dos últimos {projection.meses_media} meses · crescimento anual aplicado "
            f"{pct(projection.crescimento_anual)} "
            f"({'automático' if projection.modo_crescimento == 'average' else 'fixo'}) · "
            f"{len(reports)} competência(s) de simulação consolidada(s)."
        )
        st.dataframe(projection.historico_simulacoes, hide_index=True, width="stretch")

    st.subheader("6. Evolução e conciliação")
    st.caption(
        "Entradas e saídas vêm do Demonstrativo Mensal. A conciliação compara as entradas contábeis com a base efetivamente usada na simulação de créditos."
    )
    monthly_chart = go.Figure()
    monthly_chart.add_scatter(x=monthly.movimentos["Competência"], y=monthly.movimentos["Saídas"], name="Saídas", mode="lines+markers")
    monthly_chart.add_scatter(x=monthly.movimentos["Competência"], y=monthly.movimentos["Entradas"], name="Entradas", mode="lines+markers")
    monthly_chart.update_layout(yaxis_title="R$", height=390, margin=dict(l=20, r=20, t=30, b=20))
    st.plotly_chart(monthly_chart, width="stretch")
    if abs(reconciliation) >= 0.01:
        st.warning(
            f"A entrada de {report.periodo:%m/%Y} no Demonstrativo Mensal é {brl(monthly_inputs)}, enquanto a "
            f"simulação usa {brl(report.base_entradas_credito)}. Diferença para conciliar: {brl(reconciliation)}."
        )

    st.subheader("7. Pontos de atenção e plano de ação")
    points = pd.DataFrame(attention_points(report, monthly, pgdas_report))
    st.dataframe(points, hide_index=True, width="stretch")
    st.markdown(
        f'<div class="recommendation"><h3>Leitura recomendada</h3><p>O cenário híbrido merece prioridade na análise: '
        f'o impacto indicado pelo Domínio é de {pct(report.fase_2027["diferenca_percentual"])} em 2027 e '
        f'{pct(report.fase_2033["diferenca_percentual"])} em 2033, enquanto '
        f'{pct(report.percentual_operacoes_creditaveis)} das saídas são potencialmente sensíveis a crédito. '
        f'A decisão deve aguardar a conciliação das entradas, validação por CNPJ/CPF e confirmação das alíquotas.</p></div>',
        unsafe_allow_html=True,
    )

    st.subheader("8. Relatório Consultivo Nascel")
    report_state_key = (
        f"ai_report_{report.cnpj}_{report.periodo:%Y%m}_{projection.horizonte_meses}_"
        f"{projection.meses_media}_{projection.modo_crescimento}_{projection.crescimento_anual:.4f}"
    )
    active_report = st.session_state.get(report_state_key, intelligent_report)
    st.markdown(active_report)
    with st.expander("Enriquecer com Gemini (opcional)"):
        st.caption(
            "O relatório acima é produzido localmente. A opção abaixo envia o relatório-base para a API "
            "configurada somente quando você clicar em gerar. Não envie dados sem autorização do cliente."
        )
        api_key, api_key_source = get_gemini_api_key()
        configured_model = get_streamlit_secret("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        models_state_key = f"gemini_models_{report.cnpj}"
        verified_models = st.session_state.get(models_state_key, [])
        if api_key:
            st.success(f"Credencial carregada por {api_key_source}; o valor da chave permanece oculto.")
            st.warning(
                "Desde 19/06/2026, chaves padrão irrestritas podem ser recusadas pelo Google. "
                "Se o teste falhar por permissão, gere uma chave de autorização no Google AI Studio."
            )
        else:
            st.info(
                "Configure GOOGLE_API_KEY (preferencial) ou GEMINI_API_KEY em .streamlit/secrets.toml "
                "ou nos Secrets do Streamlit Cloud."
            )
        if st.button(
            "Testar conexão e atualizar modelos",
            disabled=not api_key,
            help="Valida a credencial e lista modelos compatíveis sem enviar os dados do relatório.",
        ):
            try:
                with st.spinner("Validando credencial e consultando modelos disponíveis..."):
                    verified_models = list_gemini_models(api_key)
                    st.session_state[models_state_key] = verified_models
                st.success(f"Conexão validada. {len(verified_models)} modelo(s) de texto disponível(is).")
            except AnalyticsError as exc:
                st.session_state.pop(models_state_key, None)
                verified_models = []
                st.error(str(exc))

        model_options = list(verified_models or GEMINI_MODEL_PREFERENCE)
        if not verified_models and configured_model not in model_options:
            model_options.insert(0, configured_model)
        selected_default = configured_model if configured_model in model_options else model_options[0]
        model = st.selectbox(
            "Modelo Gemini",
            model_options,
            index=model_options.index(selected_default),
            help="Após testar a conexão, esta lista mostra somente modelos liberados para a credencial configurada.",
        )
        if verified_models:
            st.caption("Lista confirmada diretamente pela API para esta credencial.")
        if st.button("Gerar relatório com Gemini", type="secondary", disabled=not api_key):
            try:
                with st.spinner("Analisando cenários e recomendações..."):
                    st.session_state[report_state_key] = generate_report_with_gemini(
                        intelligent_report, projection, api_key, model
                    )
                st.rerun()
            except AnalyticsError as exc:
                st.error(str(exc))
                st.info("O relatório analítico local permanece disponível e nenhum cálculo foi perdido.")
        if report_state_key in st.session_state and st.button("Restaurar relatório local"):
            del st.session_state[report_state_key]
            st.rerun()

    detail_tabs = st.tabs(["Acumuladores", "Fornecedores e clientes", "PGDAS", "Imagens executivas"])
    with detail_tabs[0]:
        left, right = st.columns(2)
        left.dataframe(report.saidas_por_acumulador, hide_index=True, width="stretch")
        right.dataframe(report.entradas_por_acumulador, hide_index=True, width="stretch")
    with detail_tabs[1]:
        left, right = st.columns(2)
        left.dataframe(report.clientes_por_regime, hide_index=True, width="stretch")
        right.dataframe(report.fornecedores_por_regime, hide_index=True, width="stretch")
    with detail_tabs[2]:
        if pgdas_report:
            pgdas_data = {
                "Empresa": pgdas_report.empresa,
                "CNPJ": format_cnpj(pgdas_report.cnpj_estabelecimento or pgdas_report.cnpj_basico),
                "Período": pgdas_report.periodo,
                "Anexo": pgdas_report.anexo,
                "RPA": brl(pgdas_report.rpa),
                "RBT12": brl(pgdas_report.rbt12),
                "DAS": brl(pgdas_report.total_das),
                "Alíquota efetiva": pct(pgdas_report.aliquota_efetiva),
            }
            st.dataframe(pd.DataFrame(pgdas_data.items(), columns=["Campo", "Valor"]), hide_index=True, width="stretch")
            st.caption(pgdas_report.atividade)
        else:
            st.info("Nenhum extrato PGDAS-D foi importado.")
    with detail_tabs[3]:
        images = create_dashboard_images(
            report, monthly, pgdas_report, projection, lc214_simulation
        )
        for name, content in images.items():
            st.image(content, caption=name, width="stretch")
            st.download_button(f"Baixar {name}", content, file_name=name, mime="image/png", key=f"download_{name}")

    excel = build_excel_dashboard(
        report,
        monthly,
        pgdas_report,
        projection=projection,
        intelligent_report=active_report,
        reports=reports,
        lc214_comparison=lc214_comparison,
        lc214_simulation=lc214_simulation,
    )
    st.download_button(
        "Baixar dashboard executivo em Excel",
        excel,
        file_name=f"Dashboard_Reforma_Tributaria_{report.periodo:%Y%m}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        width="stretch",
    )


def render_dominio_import() -> None:
    st.subheader("Importação consolidada do Domínio e PGDAS-D")
    sample_dir = Path(__file__).parent / "documentos"
    sample_available = all(
        (sample_dir / name).exists()
        for name in ("052026 - Resumido.xls", "Demonstrativo Mensal.xls", "PGDAS_TESTE.pdf")
    )
    use_samples = st.checkbox(
        "Usar os arquivos de demonstração da pasta documentos",
        value=False,
        disabled=not sample_available,
        help="Útil para teste local. Em produção, envie os arquivos da empresa analisada.",
    )
    if use_samples:
        simulation_contents = [(sample_dir / "052026 - Resumido.xls").read_bytes()]
        monthly_content = (sample_dir / "Demonstrativo Mensal.xls").read_bytes()
        pgdas_content = (sample_dir / "PGDAS_TESTE.pdf").read_bytes()
    else:
        columns = st.columns(3)
        with columns[0]:
            simulation_files = st.file_uploader(
                "Simulação da Reforma — um ou mais arquivos",
                type=["xls", "xlsx"],
                key="dominio_simulation",
                accept_multiple_files=True,
                help="Envie todas as competências disponíveis. Arquivos repetidos da mesma competência serão substituídos pelo último.",
            )
        with columns[1]:
            monthly_file = st.file_uploader("Demonstrativo Mensal.xls", type=["xls", "xlsx"], key="dominio_monthly")
        with columns[2]:
            pgdas_file = st.file_uploader("Extrato PGDAS-D (opcional)", type=["pdf"], key="pgdas_pdf")
        if not simulation_files or not monthly_file:
            st.info("Envie os dois relatórios do Domínio. O PGDAS-D é opcional, mas recomendado para validação.")
            return
        simulation_contents = [file.getvalue() for file in simulation_files]
        monthly_content = monthly_file.getvalue()
        pgdas_content = pgdas_file.getvalue() if pgdas_file else None

    try:
        reports = [
            item
            for content in simulation_contents
            for item in parse_dominio_simulations(content)
        ]
        report = max(reports, key=lambda item: item.periodo)
        monthly = parse_dominio_monthly(monthly_content)
        pgdas_report = parse_pgdas(pgdas_content) if pgdas_content else None
    except (DominioImportError, ValueError) as exc:
        st.error(str(exc))
        return

    compatible_pgdas = bool(pgdas_report and cnpjs_are_compatible(report.cnpj, pgdas_report))
    if compatible_pgdas and pgdas_report.anexo in {"I", "II", "III", "IV", "V"}:
        inferred_annex = pgdas_report.anexo
    elif report.tributos_atuais.get("IPI", 0.0) > 0:
        inferred_annex = "II"
    elif report.tributos_atuais.get("ICMS", 0.0) > 0:
        inferred_annex = "I"
    elif report.tributos_atuais.get("ISS", 0.0) > 0 and report.tributos_atuais.get("INSS/CPP", 0.0) == 0:
        inferred_annex = "IV"
    else:
        inferred_annex = "III"
    historical_until_period = monthly.movimentos[
        monthly.movimentos["Competência"].dt.to_period("M") <= report.periodo.to_period("M")
    ].tail(12)
    historical_rbt12 = float(
        (historical_until_period["Saídas"] + historical_until_period["Serviços"]).sum()
    )
    inferred_rbt12 = pgdas_report.rbt12 if compatible_pgdas else historical_rbt12

    st.subheader("Premissas legais do Simples 2027/2028")
    legal_columns = st.columns(2)
    with legal_columns[0]:
        future_annex = st.selectbox(
            "Anexo da LC 214/2025",
            ["I", "II", "III", "IV", "V"],
            index=["I", "II", "III", "IV", "V"].index(inferred_annex),
            help="Preenchido pelo PGDAS quando compatível; sem PGDAS, é apenas uma inferência e deve ser validado.",
        )
    with legal_columns[1]:
        future_rbt12 = st.number_input(
            "RBT12 para a nova tabela",
            min_value=0.01,
            max_value=4_800_000.00,
            value=min(max(inferred_rbt12, 0.01), 4_800_000.00),
            step=1_000.0,
            format="%.2f",
            help="Usa o RBT12 do PGDAS compatível; na ausência dele, soma as últimas 12 competências do Demonstrativo Mensal.",
        )
    if not compatible_pgdas:
        st.warning(
            "O Anexo e o RBT12 não vieram de um PGDAS compatível. Revise essas duas premissas antes da decisão."
        )
    if future_annex == "II":
        st.info(
            "Anexo II · critério conservador: a tabela oficial de 2027/2028 ainda inclui o IPI na partilha. "
            "Embora exista a redução geral do IPI prevista na transição, não foi identificada regra oficial "
            "que autorize retirar o IPI desta tabela ou trocar automaticamente o Anexo. O cálculo manterá o IPI "
            "até eventual norma específica."
        )
    try:
        lc214_simulation = simulate_lc214_2027_2028(
            revenue=report.base_saidas,
            rbt12=future_rbt12,
            annex=future_annex,
            regular_cbs=report.fase_2027["cbs"],
            regular_ibs=report.fase_2027["ibs"],
        )
        current_taxes = dict(pgdas_report.tributos) if compatible_pgdas else dict(report.tributos_atuais)
        current_taxes["Total"] = report.tributos_atuais["Total"]
        lc214_comparison = tax_comparison_frame(
            current_taxes,
            lc214_simulation,
            inside_total_override=report.tributos_atuais["Total"],
            outside_residual_total_override=report.fase_2027["simples_residual"],
        )
    except SimplesLC214Error as exc:
        st.error(str(exc))
        return

    st.subheader("Premissas da projeção futura")
    growth_mode_label = st.radio(
        "Forma de crescimento da projeção",
        ["Automático — média do crescimento mensal", "Percentual fixo informado"],
        horizontal=True,
        help="No modo automático, o sistema calcula a média geométrica das variações mensais, reduzindo a distorção de meses muito voláteis.",
    )
    projection_columns = st.columns(2)
    available_windows = [value for value in (3, 6, 12, 18, 24) if value <= len(monthly.movimentos)]
    if not available_windows:
        available_windows = [len(monthly.movimentos)]
    default_window = 12 if 12 in available_windows else available_windows[-1]
    with projection_columns[0]:
        average_months = st.selectbox(
            "Meses usados na média",
            available_windows,
            index=available_windows.index(default_window),
        )
    horizon_months = 12
    with projection_columns[1]:
        if growth_mode_label.startswith("Automático"):
            growth_options = [
                value
                for value in (3, 6, 12)
                if value <= max(len(monthly.movimentos) - 1, 1)
            ] or [max(len(monthly.movimentos) - 1, 1)]
            default_growth_window = 6 if 6 in growth_options else growth_options[-1]
            growth_lookback_months = st.selectbox(
                "Meses para calcular o crescimento",
                growth_options,
                index=growth_options.index(default_growth_window),
            )
            annual_growth_pct = 0.0
            growth_mode = "average"
        else:
            annual_growth_pct = st.number_input(
                "Crescimento anual esperado (%)",
                min_value=-90.0,
                max_value=300.0,
                value=0.0,
                step=1.0,
            )
            growth_lookback_months = 6
            growth_mode = "fixed"
    st.caption(
        "Períodos fixos: ano-calendário de 2027 e cenário estrutural de 2033 "
        "com a mesma base anual de receita e compras de 2027."
    )
    try:
        projection = build_future_projection(
            reports,
            monthly,
            horizon_months=horizon_months,
            average_months=average_months,
            annual_growth=annual_growth_pct / 100,
            growth_mode=growth_mode,
            growth_lookback_months=growth_lookback_months,
            lc214_simulation=lc214_simulation,
        )
        intelligent_report = generate_local_intelligent_report(
            projection, reports, monthly, pgdas_report
        )
    except AnalyticsError as exc:
        st.error(str(exc))
        return
    render_dominio_results(
        report, monthly, pgdas_report, reports, projection, intelligent_report,
        lc214_simulation, lc214_comparison,
    )


def main() -> None:
    st.set_page_config(page_title="Simulador IBS/CBS | Simples Nacional", page_icon="📊", layout="wide")
    inject_styles()
    st.markdown(
        f'<div class="nascel-brand"><img src="{NASCEL_LOGO_URL}" alt="Grupo Nascel">'
        f'<div class="nascel-brand-copy"><strong>Nascel Contabilidade</strong>{NASCEL_TAGLINE}</div></div>',
        unsafe_allow_html=True,
    )
    st.title("Diagnóstico da Reforma Tributária")
    st.markdown('<p class="subtitle">Simples Nacional · Análise consultiva Por Dentro × Fora do DAS</p>', unsafe_allow_html=True)

    import_mode = st.radio(
        "Formato dos arquivos",
        ["Relatórios consolidados do Domínio + PGDAS", "Arquivos transacionais CSV/XLSX"],
        horizontal=True,
        help="O primeiro modo lê diretamente os relatórios disponibilizados na pasta documentos.",
    )
    if import_mode == "Relatórios consolidados do Domínio + PGDAS":
        render_dominio_import()
        return

    with st.sidebar:
        st.header("Premissas da simulação")
        reference_pct = st.number_input(
            "Alíquota combinada estimada IBS/CBS (%)", min_value=0.0, max_value=100.0,
            value=REFERENCE_IBS_CBS_RATE * 100, step=0.1,
            help="Premissa de cenário estrutural. Não representa automaticamente as alíquotas de cada ano da transição.",
        )
        das_share_pct = st.number_input(
            "Participação de IBS/CBS na alíquota do DAS (%)", min_value=0.0, max_value=100.0,
            value=DEFAULT_DAS_IBS_CBS_SHARE * 100, step=0.5,
            help="Percentual da alíquota efetiva do Simples atribuído a IBS/CBS. O padrão é apenas ilustrativo; ajuste conforme anexo, faixa e fase de transição.",
        )
        contract_loss_pct = st.slider(
            "Receita de contratos em risco (% das vendas positivas)", 0.0, 50.0, 5.0, 0.5,
            help="Parcela da receita que pode ser perdida pela menor transferência de créditos.",
        )
        contribution_margin_pct = st.slider(
            "Margem de contribuição dos contratos (%)", 0.0, 100.0, 30.0, 1.0,
            help="Converte a receita em risco em prejuízo econômico comparável ao impacto tributário.",
        )
        st.info(
            "As alíquotas são premissas editáveis. O direito ao crédito depende do documento fiscal, "
            "da operação e da extinção do débito. Confirme a legislação e a fase de transição."
        )
        st.markdown(
            "[LC 214/2025 — texto compilado](https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp214compilado.htm)  \n"
            "[Calendário do Simples para 2027](https://www8.receita.fazenda.gov.br/SimplesNacional/Noticias/NoticiaCompleta.aspx?id=c739e03c-8482-473f-8e82-f38ec3b13637)"
        )

    st.subheader("1. Importação dos arquivos")
    st.download_button(
        "Baixar modelo Excel para preenchimento",
        data=build_transactional_template(),
        file_name="Modelo_Importacao_Transacional_IBS_CBS.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help="O modelo contém as abas Faturamento, Entradas, Parametros_SN e instruções de preenchimento.",
    )
    combined_template_file = st.file_uploader(
        "Importar modelo Excel preenchido (arquivo único com todas as abas)",
        type=["xlsx"],
        key="transactional_combined_template",
    )
    st.caption("Alternativamente, envie os três arquivos separados abaixo.")
    upload_columns = st.columns(3)
    with upload_columns[0]:
        faturamento_file = st.file_uploader("Faturamento", type=["csv", "xlsx", "xls"], key="faturamento")
        st.caption("Data · CFOP · CNPJ/CPF do cliente · Valor total")
    with upload_columns[1]:
        entradas_file = st.file_uploader("Entradas", type=["csv", "xlsx", "xls"], key="entradas")
        st.caption("Data · CFOP · Valor da nota · Base de crédito")
    with upload_columns[2]:
        parametros_file = st.file_uploader("Parâmetros do Simples", type=["csv", "xlsx", "xls"], key="parametros")
        st.caption("RBT12 · Anexo · Alíquota efetiva atual")

    if not combined_template_file and not all((faturamento_file, entradas_file, parametros_file)):
        st.info("Envie os três arquivos para liberar a análise comparativa.")
        with st.expander("Ver regras de leitura e classificação"):
            st.markdown(
                "- CSVs podem usar vírgula, ponto e vírgula, tabulação ou barra vertical.\n"
                "- Valores como `1.234,56`, `R$ 1.234,56` e `1234.56` são aceitos.\n"
                "- Documentos com até 11 dígitos são classificados como PF; acima de 11, como PJ.\n"
                "- Linhas duplicadas são sinalizadas e mantidas, evitando alteração automática da escrituração."
            )
        return

    try:
        if combined_template_file:
            template_content = io.BytesIO(combined_template_file.getvalue())
            raw_sales = pd.read_excel(template_content, sheet_name="Faturamento", dtype=object, engine="calamine")
            template_content.seek(0)
            raw_inputs = pd.read_excel(template_content, sheet_name="Entradas", dtype=object, engine="calamine")
            template_content.seek(0)
            raw_params = pd.read_excel(template_content, sheet_name="Parametros_SN", dtype=object, engine="calamine")
        else:
            raw_sales = read_uploaded_table(faturamento_file.name, faturamento_file.getvalue())
            raw_inputs = read_uploaded_table(entradas_file.name, entradas_file.getvalue())
            raw_params = read_uploaded_table(parametros_file.name, parametros_file.getvalue())
        sales, sales_warnings = prepare_transaction_data(raw_sales, "faturamento")
        purchases, purchase_warnings = prepare_transaction_data(raw_inputs, "entradas")
        rbt12, anexo, effective_rate, parameter_warnings = prepare_parameters(raw_params)

        simulation_inputs = SimulationInputs(
            faturamento=sales,
            entradas=purchases,
            rbt12=rbt12,
            anexo=anexo,
            aliquota_efetiva=effective_rate,
            aliquota_referencia=reference_pct / 100,
            fracao_ibs_cbs_das=das_share_pct / 100,
            perda_contratos_percentual=contract_loss_pct / 100,
            margem_contribuicao=contribution_margin_pct / 100,
        )
        result = run_simulation(simulation_inputs)
    except DataValidationError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"Falha inesperada ao processar os arquivos: {exc}")
        return

    show_warnings((
        ("Faturamento", sales_warnings),
        ("Entradas", purchase_warnings),
        ("Parâmetros", parameter_warnings),
    ))

    st.divider()
    st.subheader("2. Resultado executivo")
    metrics = st.columns(3)
    metrics[0].metric("Vendas para PJ", pct(result.percentual_b2b), help=brl(result.vendas_b2b))
    metrics[1].metric("Carga tributária · Por Dentro", pct(result.carga_1), help=brl(result.cenario_1_das))
    metrics[2].metric(
        "Carga tributária · Híbrido", pct(result.carga_2),
        delta=pct(result.carga_2 - result.carga_1), delta_color="inverse",
        help=brl(result.cenario_2_total),
    )

    chart_columns = st.columns(2)
    with chart_columns[0]:
        st.plotly_chart(comparison_chart(result), width="stretch")
    with chart_columns[1]:
        st.plotly_chart(credit_chart(result, simulation_inputs.aliquota_referencia), width="stretch")

    st.subheader("Composição dos cenários")
    composition = pd.DataFrame({
        "Componente": [
            "DAS total (Por Dentro)", "Crédito repassado B2B (Por Dentro)",
            "DAS residual (Híbrido)", "IBS/CBS bruto (Híbrido)",
            "Créditos disponíveis (Híbrido)", "(-) Créditos utilizados no período",
            "Saldo estimado de créditos", "Total tributário (Híbrido)",
        ],
        "Valor": [
            result.cenario_1_das, result.cenario_1_credito_repassado,
            result.cenario_2_das_residual, result.cenario_2_ibs_cbs_bruto,
            result.cenario_2_creditos_disponiveis, -result.cenario_2_creditos_utilizados,
            result.cenario_2_saldo_creditos, result.cenario_2_total,
        ],
    })
    st.dataframe(
        composition,
        hide_index=True,
        width="stretch",
        column_config={"Valor": st.column_config.NumberColumn("Valor", format="R$ %.2f")},
    )

    st.subheader("3. Recomendação do sistema")
    st.markdown(
        f'<div class="recommendation"><h3>{result.recomendacao}</h3>'
        f'<p>{result.justificativa}</p></div>',
        unsafe_allow_html=True,
    )
    decision = pd.DataFrame({
        "Critério": [
            "Dependência B2B", "Impacto incremental do híbrido", "Receita de contratos em risco",
            "Prejuízo econômico estimado",
        ],
        "Resultado": [
            pct(result.percentual_b2b), brl(result.impacto_hibrido),
            brl(result.receita_contratos_em_risco), brl(result.perda_contratos_estimada),
        ],
    })
    st.dataframe(decision, hide_index=True, width="stretch")

    with st.expander("Auditoria dos dados processados"):
        st.write(f"Período do faturamento: {sales['Data'].min():%d/%m/%Y} a {sales['Data'].max():%d/%m/%Y}" if sales["Data"].notna().any() else "Período não identificado")
        st.write(f"RBT12: {brl(rbt12)} · Anexo: {anexo} · Alíquota efetiva: {pct(effective_rate)}")
        tab_sales, tab_purchases = st.tabs(["Faturamento tratado", "Entradas tratadas"])
        tab_sales.dataframe(sales, width="stretch", hide_index=True)
        tab_purchases.dataframe(purchases, width="stretch", hide_index=True)

    try:
        pdf = build_pdf(result, simulation_inputs)
        st.download_button(
            "Baixar relatório executivo em PDF",
            data=pdf,
            file_name=f"simulacao_ibs_cbs_{datetime.now():%Y%m%d}.pdf",
            mime="application/pdf",
            type="primary",
            width="stretch",
        )
    except DataValidationError as exc:
        st.warning(str(exc))


if __name__ == "__main__":
    main()
