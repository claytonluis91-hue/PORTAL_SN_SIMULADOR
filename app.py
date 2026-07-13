"""Portal de Simulação da Reforma Tributária (IBS/CBS).

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
    build_future_projection,
    generate_local_intelligent_report,
    generate_report_with_ai,
)
from dashboard_exports import (
    attention_points,
    build_excel_dashboard,
    build_transactional_template,
    create_dashboard_images,
    scenario_table,
)
from dominio_importers import (
    DominioImportError,
    DominioSimulationReport,
    MonthlyReport,
    PGDASReport,
    cnpjs_are_compatible,
    parse_dominio_monthly,
    parse_dominio_simulation,
    parse_pgdas,
)


REFERENCE_IBS_CBS_RATE = 0.265
DEFAULT_DAS_IBS_CBS_SHARE = 0.35


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
        title="Simulação da Reforma Tributária - IBS/CBS",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CenteredTitle", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor("#123B5D")))
    story: list[object] = [
        Paragraph("Simulação da Reforma Tributária", styles["CenteredTitle"]),
        Paragraph("Simples Nacional — IBS/CBS", styles["Heading2"]),
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
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123B5D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FAFC")),
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
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8F0F7")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([
        Paragraph(f"Recomendação: {result.recomendacao}", styles["Heading2"]),
        Paragraph(result.justificativa, styles["BodyText"]),
        Spacer(1, 6 * mm),
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
        marker_color=["#2F80ED", "#16A085"],
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
        marker_color=["#7B61FF", "#F2C94C"],
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
        .block-container {max-width: 1200px; padding-top: 2rem; padding-bottom: 3rem;}
        [data-testid="stMetric"] {background: #f7fafc; border: 1px solid #dbe5ee; padding: 1rem; border-radius: 12px;}
        .recommendation {padding: 1.2rem 1.4rem; border-radius: 12px; background: #eaf7f2; border-left: 6px solid #16a085;}
        .subtitle {color: #526579; margin-top: -0.7rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def format_cnpj(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return value


def render_dominio_results(
    report: DominioSimulationReport,
    monthly: MonthlyReport,
    pgdas_report: PGDASReport | None,
    reports: list[DominioSimulationReport],
    projection: FutureProjection,
    intelligent_report: str,
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

    st.subheader("Resultado consolidado do Domínio")
    metrics = st.columns(5)
    metrics[0].metric("Saídas analisadas", brl(report.base_saidas))
    metrics[1].metric("Base de entradas", brl(report.base_entradas_credito))
    metrics[2].metric("Operações potencialmente creditáveis", pct(report.percentual_operacoes_creditaveis))
    metrics[3].metric(
        "Impacto Híbrido 2027", brl(report.fase_2027["diferenca"]),
        delta=pct(report.fase_2027["diferenca_percentual"]), delta_color="inverse",
    )
    metrics[4].metric(
        "Impacto Híbrido 2033", brl(report.fase_2033["diferenca"]),
        delta=pct(report.fase_2033["diferenca_percentual"]), delta_color="inverse",
    )

    scenarios = scenario_table(report)
    chart_columns = st.columns(2)
    with chart_columns[0]:
        figure = go.Figure(go.Bar(
            x=scenarios["Cenário"], y=scenarios["Carga Tributária"],
            marker_color=["#526579", "#2F80ED", "#16A085", "#F2C94C"],
            text=[brl(value) for value in scenarios["Carga Tributária"]], textposition="outside",
        ))
        figure.update_layout(title="Carga tributária por cenário", yaxis_title="R$", height=430, margin=dict(l=20, r=20, t=60, b=90))
        st.plotly_chart(figure, width="stretch")
    with chart_columns[1]:
        figure = go.Figure(go.Bar(
            x=scenarios["Cenário"], y=scenarios["Crédito Potencial ao Cliente"],
            marker_color="#7B61FF",
            text=[brl(value) for value in scenarios["Crédito Potencial ao Cliente"]], textposition="outside",
        ))
        figure.update_layout(title="Crédito potencial em operações creditáveis", yaxis_title="R$", height=430, margin=dict(l=20, r=20, t=60, b=90))
        st.plotly_chart(figure, width="stretch")

    scenarios_display = scenarios.copy()
    scenarios_display["Carga Efetiva"] = scenarios_display["Carga Efetiva"] * 100
    st.dataframe(
        scenarios_display,
        hide_index=True,
        width="stretch",
        column_config={
            "Carga Tributária": st.column_config.NumberColumn(format="R$ %.2f"),
            "Carga Efetiva": st.column_config.NumberColumn(format="%.2f%%"),
            "Crédito Potencial ao Cliente": st.column_config.NumberColumn(format="R$ %.2f"),
            "Variação vs. Atual": st.column_config.NumberColumn(format="R$ %.2f"),
        },
    )

    st.subheader("Projeção dos períodos futuros")
    totals = projection.totais
    projection_metrics = st.columns(4)
    projection_metrics[0].metric(
        f"Receita projetada · {projection.horizonte_meses} meses", brl(totals["receita"])
    )
    projection_metrics[1].metric("Por Dentro projetado", brl(totals["por_dentro"]))
    projection_metrics[2].metric(
        "Híbrido 2027 projetado",
        brl(totals["hibrido_2027"]),
        delta=brl(totals["hibrido_2027"] - totals["por_dentro"]),
        delta_color="inverse",
    )
    projection_metrics[3].metric(
        "Híbrido 2033 projetado",
        brl(totals["hibrido_2033"]),
        delta=brl(totals["hibrido_2033"] - totals["por_dentro"]),
        delta_color="inverse",
    )
    projection_chart = go.Figure()
    for column, color in (
        ("Por Dentro", "#2F80ED"),
        ("Híbrido 2027", "#16A085"),
        ("Híbrido 2033", "#F2C94C"),
    ):
        projection_chart.add_scatter(
            x=projection.projecao_mensal["Competência"],
            y=projection.projecao_mensal[column],
            name=column,
            mode="lines+markers",
            line=dict(color=color),
        )
    projection_chart.update_layout(
        yaxis_title="Carga tributária estimada (R$)",
        height=410,
        margin=dict(l=20, r=20, t=30, b=20),
    )
    st.plotly_chart(projection_chart, width="stretch")
    with st.expander("Ver memória da projeção e períodos importados"):
        st.caption(
            f"Média dos últimos {projection.meses_media} meses · crescimento anual aplicado "
            f"{pct(projection.crescimento_anual)} "
            f"({'automático' if projection.modo_crescimento == 'average' else 'fixo'}) · "
            f"{len(reports)} competência(s) de simulação consolidada(s)."
        )
        st.dataframe(projection.historico_simulacoes, hide_index=True, width="stretch")
        st.dataframe(projection.projecao_mensal, hide_index=True, width="stretch")

    st.subheader("Evolução e conciliação")
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

    st.subheader("Pontos de atenção e plano de ação")
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

    st.subheader("Relatório inteligente de possibilidades")
    report_state_key = (
        f"ai_report_{report.cnpj}_{report.periodo:%Y%m}_{projection.horizonte_meses}_"
        f"{projection.meses_media}_{projection.modo_crescimento}_{projection.crescimento_anual:.4f}"
    )
    active_report = st.session_state.get(report_state_key, intelligent_report)
    st.markdown(active_report)
    with st.expander("Enriquecer com IA generativa (opcional)"):
        st.caption(
            "O relatório acima é produzido localmente. A opção abaixo envia o relatório-base para a API "
            "configurada somente quando você clicar em gerar. Não envie dados sem autorização do cliente."
        )
        configured_key = os.getenv("OPENAI_API_KEY", "")
        api_key = st.text_input(
            "Chave da API",
            value="" if not configured_key else configured_key,
            type="password",
            help="Pode ser configurada pela variável OPENAI_API_KEY.",
        )
        model = st.text_input(
            "Modelo",
            value=os.getenv("OPENAI_MODEL", ""),
            placeholder="Informe o modelo autorizado em sua conta",
        )
        if st.button("Gerar relatório com IA", type="secondary"):
            try:
                with st.spinner("Analisando cenários e recomendações..."):
                    st.session_state[report_state_key] = generate_report_with_ai(
                        intelligent_report, projection, api_key, model
                    )
                st.rerun()
            except AnalyticsError as exc:
                st.error(str(exc))
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
        images = create_dashboard_images(report, monthly, pgdas_report, projection)
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
        reports = [parse_dominio_simulation(content) for content in simulation_contents]
        report = max(reports, key=lambda item: item.periodo)
        monthly = parse_dominio_monthly(monthly_content)
        pgdas_report = parse_pgdas(pgdas_content) if pgdas_content else None
    except (DominioImportError, ValueError) as exc:
        st.error(str(exc))
        return

    st.subheader("Premissas da projeção futura")
    growth_mode_label = st.radio(
        "Forma de crescimento da projeção",
        ["Automático — média do crescimento mensal", "Percentual fixo informado"],
        horizontal=True,
        help="No modo automático, o sistema calcula a média geométrica das variações mensais, reduzindo a distorção de meses muito voláteis.",
    )
    projection_columns = st.columns(3)
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
    with projection_columns[1]:
        horizon_months = st.slider("Horizonte projetado (meses)", 6, 36, 12, 1)
    with projection_columns[2]:
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
    try:
        projection = build_future_projection(
            reports,
            monthly,
            horizon_months=horizon_months,
            average_months=average_months,
            annual_growth=annual_growth_pct / 100,
            growth_mode=growth_mode,
            growth_lookback_months=growth_lookback_months,
        )
        intelligent_report = generate_local_intelligent_report(
            projection, reports, monthly, pgdas_report
        )
    except AnalyticsError as exc:
        st.error(str(exc))
        return
    render_dominio_results(
        report, monthly, pgdas_report, reports, projection, intelligent_report
    )


def main() -> None:
    st.set_page_config(page_title="Simulador IBS/CBS | Simples Nacional", page_icon="📊", layout="wide")
    inject_styles()
    st.title("Portal de Simulação da Reforma Tributária")
    st.markdown('<p class="subtitle">Simples Nacional · Comparativo Por Dentro × Híbrido</p>', unsafe_allow_html=True)

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
