"""Geração de dashboards executivos em XLSX e PNG."""

from __future__ import annotations

import io
from dataclasses import asdict
from typing import Any, Sequence

import pandas as pd

from analytics_engine import FutureProjection, build_future_projection, generate_local_intelligent_report
from dominio_importers import (
    DominioSimulationReport,
    MonthlyReport,
    PGDASReport,
    cnpjs_are_compatible,
)


COLORS = {
    "navy": "#123B5D",
    "blue": "#2F80ED",
    "green": "#16A085",
    "yellow": "#F2C94C",
    "red": "#C0392B",
    "slate": "#526579",
    "light": "#F4F7FA",
}


def scenario_table(report: DominioSimulationReport) -> pd.DataFrame:
    revenue = report.base_saidas
    current_total = report.tributos_atuais["Total"]
    effective_rate = current_total / revenue if revenue else 0.0
    replaced_taxes = sum(
        report.tributos_atuais.get(tax, 0.0) for tax in ("ICMS", "ISS", "PIS/Pasep", "COFINS")
    )
    das_ibs_cbs_share = replaced_taxes / current_total if current_total else 0.0
    credit_sensitive_sales = revenue * report.percentual_operacoes_creditaveis
    inside_credit = credit_sensitive_sales * effective_rate * das_ibs_cbs_share
    credit_2027 = credit_sensitive_sales * (
        report.aliquota_cbs_2027 + report.aliquota_ibs_2027
    )
    credit_2033 = credit_sensitive_sales * (
        report.aliquota_cbs_2033 + report.aliquota_ibs_2033
    )
    rows = [
        {
            "Cenário": "Atual — base de comparação",
            "Carga Tributária": current_total,
            "Carga Efetiva": effective_rate,
            "Crédito Potencial ao Cliente": 0.0,
            "Variação vs. Atual": 0.0,
            "Leitura": "Referência atual",
        },
        {
            "Cenário": "Simples Por Dentro — proxy",
            "Carga Tributária": current_total,
            "Carga Efetiva": effective_rate,
            "Crédito Potencial ao Cliente": inside_credit,
            "Variação vs. Atual": 0.0,
            "Leitura": "Menor complexidade; crédito limitado ao montante dentro do DAS",
        },
        {
            "Cenário": "Híbrido 2027 — Domínio",
            "Carga Tributária": report.fase_2027["total"],
            "Carga Efetiva": report.fase_2027["total"] / revenue if revenue else 0.0,
            "Crédito Potencial ao Cliente": credit_2027,
            "Variação vs. Atual": report.fase_2027["diferenca"],
            "Leitura": "Aumento pequeno de carga e maior crédito comercial",
        },
        {
            "Cenário": "Híbrido 2033 — Domínio",
            "Carga Tributária": report.fase_2033["total"],
            "Carga Efetiva": report.fase_2033["total"] / revenue if revenue else 0.0,
            "Crédito Potencial ao Cliente": credit_2033,
            "Variação vs. Atual": report.fase_2033["diferenca"],
            "Leitura": "Menor carga estimada, sujeita à confirmação das alíquotas futuras",
        },
    ]
    return pd.DataFrame(rows)


def attention_points(
    report: DominioSimulationReport,
    monthly: MonthlyReport,
    pgdas: PGDASReport | None = None,
) -> list[dict[str, str]]:
    period_rows = monthly.movimentos[
        monthly.movimentos["Competência"].dt.to_period("M") == report.periodo.to_period("M")
    ]
    monthly_inputs = float(period_rows["Entradas"].sum()) if not period_rows.empty else 0.0
    reconciliation = monthly_inputs - report.base_entradas_credito
    points: list[dict[str, str]] = []
    if pgdas is not None and not cnpjs_are_compatible(report.cnpj, pgdas):
        points.append(
            {
                "Prioridade": "CRÍTICO",
                "Tema": "CNPJ divergente no PGDAS",
                "Constatação": f"Domínio: {report.cnpj} | PGDAS: {pgdas.cnpj_estabelecimento or pgdas.cnpj_basico}",
                "Ação": "Não consolidar os arquivos. Importar o PGDAS da mesma empresa antes da decisão.",
            }
        )
    if abs(reconciliation) >= 0.01:
        points.append(
            {
                "Prioridade": "ATENÇÃO",
                "Tema": "Conciliação das entradas",
                "Constatação": f"Demonstrativo mensal menos base de crédito: R$ {reconciliation:,.2f}",
                "Ação": "Identificar acumuladores excluídos e confirmar quais aquisições geram crédito.",
            }
        )
    points.extend(
        [
            {
                "Prioridade": "ATENÇÃO",
                "Tema": "Dependência B2B estimada",
                "Constatação": f"{report.percentual_operacoes_creditaveis:.2%} das saídas não estão no acumulador 'não contribuinte'.",
                "Ação": "Validar com relatório por CNPJ/CPF; o acumulador é apenas uma proxy de crédito comercial.",
            },
            {
                "Prioridade": "CRÍTICO",
                "Tema": "Elegibilidade dos créditos",
                "Constatação": f"A simulação usa R$ {report.base_entradas_credito:,.2f} como base de entradas.",
                "Ação": "Revisar brindes, uso/consumo, documentos inidôneos, pagamentos e operações com tratamento específico.",
            },
            {
                "Prioridade": "ATENÇÃO",
                "Tema": "Alíquotas futuras",
                "Constatação": (
                    f"Domínio: CBS {report.aliquota_cbs_2033:.2%} e IBS {report.aliquota_ibs_2033:.2%} em 2033."
                ),
                "Ação": "Atualizar a simulação quando as alíquotas de referência e do destino forem publicadas.",
            },
            {
                "Prioridade": "DECISÃO",
                "Tema": "Opção 2027",
                "Constatação": f"Híbrido varia R$ {report.fase_2027['diferenca']:,.2f} ({report.fase_2027['diferenca_percentual']:.2%}).",
                "Ação": "Comparar o pequeno impacto de carga com retenção de clientes B2B e custo operacional.",
            },
            {
                "Prioridade": "DECISÃO",
                "Tema": "Cenário estrutural 2033",
                "Constatação": f"Híbrido varia R$ {report.fase_2033['diferenca']:,.2f} ({report.fase_2033['diferenca_percentual']:.2%}).",
                "Ação": "Planejar cadastro de itens, fornecedores, destinos, documentos e conciliação de créditos.",
            },
        ]
    )
    return points


def create_dashboard_images(
    report: DominioSimulationReport,
    monthly: MonthlyReport,
    pgdas: PGDASReport | None = None,
    projection: FutureProjection | None = None,
) -> dict[str, bytes]:
    import os
    import tempfile
    from pathlib import Path

    cache_dir = Path(tempfile.gettempdir()) / "portal_ibs_cbs_matplotlib"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    scenarios = scenario_table(report)
    projection = projection or build_future_projection([report], monthly)
    images: dict[str, bytes] = {}

    fig, ax1 = plt.subplots(figsize=(12, 6.5), facecolor="white")
    names = ["Atual", "Por Dentro\n(proxy)", "Híbrido\n2027", "Híbrido\n2033"]
    x = range(len(names))
    bars = ax1.bar(
        x,
        scenarios["Carga Tributária"],
        color=[COLORS["slate"], COLORS["blue"], COLORS["green"], COLORS["yellow"]],
        width=0.62,
    )
    ax1.set_xticks(list(x), names)
    ax1.set_ylabel("Carga tributária (R$)", color=COLORS["navy"])
    ax1.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"R$ {value:,.0f}"))
    ax1.grid(axis="y", alpha=0.18)
    ax1.set_axisbelow(True)
    for bar, value in zip(bars, scenarios["Carga Tributária"]):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 55, f"R$ {value:,.2f}", ha="center", fontsize=9)
    ax2 = ax1.twinx()
    ax2.plot(
        list(x), scenarios["Crédito Potencial ao Cliente"], color=COLORS["red"], marker="o", linewidth=2.5,
        label="Crédito potencial ao cliente",
    )
    ax2.set_ylabel("Crédito potencial (R$)", color=COLORS["red"])
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"R$ {value:,.0f}"))
    fig.suptitle("Comparativo dos cenários tributários", fontsize=18, fontweight="bold", color=COLORS["navy"])
    ax1.set_title(f"{report.empresa} · competência {report.periodo:%m/%Y}", color=COLORS["slate"])
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    images["dashboard_cenarios.png"] = buffer.getvalue()

    movements = monthly.movimentos.copy()
    fig, ax = plt.subplots(figsize=(12, 6.5), facecolor="white")
    ax.plot(movements["Competência"], movements["Saídas"], marker="o", color=COLORS["blue"], label="Saídas")
    ax.plot(movements["Competência"], movements["Entradas"], marker="o", color=COLORS["green"], label="Entradas")
    ax.fill_between(movements["Competência"], movements["Saídas"], alpha=0.08, color=COLORS["blue"])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"R$ {value/1000:.0f} mil"))
    ax.grid(alpha=0.2)
    ax.legend(frameon=False, ncol=2)
    ax.set_title("Evolução mensal de entradas e saídas", fontsize=18, fontweight="bold", color=COLORS["navy"])
    ax.set_xlabel("Competência")
    ax.set_ylabel("Movimentação")
    fig.autofmt_xdate()
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    images["dashboard_evolucao_mensal.png"] = buffer.getvalue()

    points = attention_points(report, monthly, pgdas)
    fig, ax = plt.subplots(figsize=(13, 7.5), facecolor="white")
    ax.axis("off")
    ax.text(0.02, 0.95, "Pontos de atenção para a decisão", fontsize=21, fontweight="bold", color=COLORS["navy"])
    ax.text(0.02, 0.90, report.empresa, fontsize=12, color=COLORS["slate"])
    y = 0.82
    priority_colors = {"CRÍTICO": COLORS["red"], "ATENÇÃO": "#D68910", "DECISÃO": COLORS["blue"]}
    for point in points[:6]:
        color = priority_colors.get(point["Prioridade"], COLORS["slate"])
        ax.text(0.02, y, point["Prioridade"], fontsize=9, fontweight="bold", color="white", bbox=dict(boxstyle="round,pad=0.35", facecolor=color, edgecolor="none"))
        ax.text(0.15, y + 0.006, point["Tema"], fontsize=12, fontweight="bold", color=COLORS["navy"])
        ax.text(0.15, y - 0.035, point["Constatação"], fontsize=9.5, color=COLORS["slate"])
        ax.text(0.15, y - 0.068, "Ação: " + point["Ação"], fontsize=9.5, color="#263238", wrap=True)
        y -= 0.13
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    images["dashboard_pontos_atencao.png"] = buffer.getvalue()

    projected = projection.projecao_mensal
    fig, ax = plt.subplots(figsize=(12, 6.5), facecolor="white")
    ax.plot(projected["Competência"], projected["Por Dentro"], marker="o", color=COLORS["blue"], label="Por Dentro")
    ax.plot(projected["Competência"], projected["Híbrido 2027"], marker="o", color=COLORS["green"], label="Híbrido 2027")
    ax.plot(projected["Competência"], projected["Híbrido 2033"], marker="o", color=COLORS["yellow"], label="Híbrido 2033")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"R$ {value:,.0f}"))
    ax.grid(alpha=0.2)
    ax.legend(frameon=False, ncol=3)
    ax.set_title(
        f"Projeção tributária futura — média de {projection.meses_media} meses · crescimento {'automático' if projection.modo_crescimento == 'average' else 'fixo'} {projection.crescimento_anual:.2%} a.a.",
        fontsize=18,
        fontweight="bold",
        color=COLORS["navy"],
    )
    ax.set_xlabel("Competência projetada")
    ax.set_ylabel("Carga tributária estimada")
    fig.autofmt_xdate()
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    images["dashboard_projecao_futura.png"] = buffer.getvalue()
    return images


def _build_excel_dashboard_openpyxl(
    report: DominioSimulationReport,
    monthly: MonthlyReport,
    pgdas: PGDASReport | None = None,
) -> bytes:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.drawing.image import Image
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    wb = Workbook()
    wb.remove(wb.active)
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    ws_dashboard = wb.create_sheet("Dashboard")
    ws_scenarios = wb.create_sheet("Cenarios")
    ws_sensitivity = wb.create_sheet("Sensibilidade")
    ws_monthly = wb.create_sheet("Evolucao_Mensal")
    ws_composition = wb.create_sheet("Composicao")
    ws_attention = wb.create_sheet("Pontos_Atencao")
    ws_raw = wb.create_sheet("Dados_Dominio")
    ws_assumptions = wb.create_sheet("Premissas")
    if pgdas is not None:
        ws_pgdas = wb.create_sheet("PGDAS_Exemplo")

    navy_fill = PatternFill("solid", fgColor="123B5D")
    blue_fill = PatternFill("solid", fgColor="DCEAF7")
    green_fill = PatternFill("solid", fgColor="DFF3EC")
    yellow_fill = PatternFill("solid", fgColor="FFF4CC")
    red_fill = PatternFill("solid", fgColor="FDE2E0")
    white_font = Font(color="FFFFFF", bold=True)
    title_font = Font(size=22, bold=True, color="123B5D")
    subtitle_font = Font(size=11, color="526579")
    thin = Side(style="thin", color="D7E0E8")
    money_format = 'R$ #,##0.00;[Red]-R$ #,##0.00'
    percent_format = "0.00%"

    def style_header(sheet: Any, row: int, start: int, end: int) -> None:
        for column in range(start, end + 1):
            cell = sheet.cell(row=row, column=column)
            cell.fill = navy_fill
            cell.font = white_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = Border(bottom=thin)

    def autofit(sheet: Any, maximum: int = 45) -> None:
        for column_cells in sheet.columns:
            width = min(max((len(str(cell.value)) if cell.value is not None else 0) for cell in column_cells) + 2, maximum)
            sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = max(width, 11)

    scenarios = scenario_table(report)
    points = attention_points(report, monthly, pgdas)
    images = create_dashboard_images(report, monthly, pgdas)
    period_row = monthly.movimentos[
        monthly.movimentos["Competência"].dt.to_period("M") == report.periodo.to_period("M")
    ]
    monthly_inputs = float(period_row["Entradas"].sum()) if not period_row.empty else 0.0
    reconciliation = monthly_inputs - report.base_entradas_credito

    ws_dashboard.sheet_view.showGridLines = False
    ws_dashboard.merge_cells("A1:L2")
    ws_dashboard["A1"] = "Dashboard Executivo — Reforma Tributária IBS/CBS"
    ws_dashboard["A1"].font = title_font
    ws_dashboard["A1"].alignment = Alignment(vertical="center")
    ws_dashboard.merge_cells("A3:L3")
    ws_dashboard["A3"] = f"{report.empresa} | CNPJ {report.cnpj} | Competência {report.periodo:%m/%Y}"
    ws_dashboard["A3"].font = subtitle_font
    kpis = [
        ("A5", "C5", "Saídas analisadas", report.base_saidas, money_format, blue_fill),
        ("D5", "F5", "Base de entradas", report.base_entradas_credito, money_format, green_fill),
        ("G5", "I5", "Operações potencialmente creditáveis", report.percentual_operacoes_creditaveis, percent_format, yellow_fill),
        ("J5", "L5", "Diferença 2027", report.fase_2027["diferenca_percentual"], percent_format, red_fill if report.fase_2027["diferenca"] > 0 else green_fill),
    ]
    for start, end, label, value, number_format, fill in kpis:
        ws_dashboard.merge_cells(f"{start}:{end}")
        cell = ws_dashboard[start]
        cell.value = f"{label}\n{value}"
        cell.number_format = number_format
        cell.fill = fill
        cell.font = Font(size=12, bold=True, color="123B5D")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws_dashboard.row_dimensions[5].height = 52

    scenario_image = Image(io.BytesIO(images["dashboard_cenarios.png"]))
    scenario_image.width, scenario_image.height = 880, 475
    ws_dashboard.add_image(scenario_image, "A8")
    ws_dashboard["A33"] = "Recomendação executiva"
    ws_dashboard["A33"].font = Font(size=16, bold=True, color="123B5D")
    ws_dashboard.merge_cells("A34:L37")
    recommendation = (
        "Priorizar a análise do regime híbrido: o relatório do Domínio indica variação de apenas "
        f"{report.fase_2027['diferenca_percentual']:.2%} em 2027 e {report.fase_2033['diferenca_percentual']:.2%} em 2033, "
        f"enquanto aproximadamente {report.percentual_operacoes_creditaveis:.2%} das saídas podem ser sensíveis a crédito. "
        "A decisão final depende da conciliação das entradas, validação por cliente e confirmação das alíquotas aplicáveis."
    )
    ws_dashboard["A34"] = recommendation
    ws_dashboard["A34"].alignment = Alignment(wrap_text=True, vertical="top")
    ws_dashboard["A34"].fill = green_fill
    ws_dashboard["A34"].border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for column in range(1, 13):
        ws_dashboard.column_dimensions[get_column_letter(column)].width = 13

    scenario_headers = list(scenarios.columns)
    ws_scenarios.append(scenario_headers)
    for row in scenarios.itertuples(index=False, name=None):
        ws_scenarios.append(list(row))
    style_header(ws_scenarios, 1, 1, len(scenario_headers))
    for row in range(2, ws_scenarios.max_row + 1):
        for column in (2, 4, 5):
            ws_scenarios.cell(row, column).number_format = money_format
        ws_scenarios.cell(row, 3).number_format = percent_format
    scenario_table_ref = Table(displayName="TabelaCenarios", ref=f"A1:F{ws_scenarios.max_row}")
    scenario_table_ref.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws_scenarios.add_table(scenario_table_ref)
    ws_scenarios.freeze_panes = "A2"
    autofit(ws_scenarios, 62)

    assumptions = [
        ("Receita base", report.base_saidas, "Domínio — débitos pelas saídas"),
        ("Base de crédito", report.base_entradas_credito, "Domínio — créditos pelas entradas"),
        ("Alíquota efetiva atual", report.tributos_atuais["Total"] / report.base_saidas, "Carga atual / saídas"),
        ("DAS residual 2033", report.fase_2033["simples_residual"] / report.base_saidas, "Domínio"),
        ("CBS 2033", report.aliquota_cbs_2033, "Domínio"),
        ("IBS 2033", report.aliquota_ibs_2033, "Domínio"),
        ("Receita de contratos em risco", 0.05, "Premissa editável"),
        ("Margem de contribuição", 0.30, "Premissa editável"),
    ]
    ws_assumptions.append(["Premissa", "Valor", "Fonte/observação"])
    for row in assumptions:
        ws_assumptions.append(row)
    style_header(ws_assumptions, 1, 1, 3)
    for row in range(2, ws_assumptions.max_row + 1):
        ws_assumptions.cell(row, 2).fill = yellow_fill if row >= 8 else blue_fill
        if row == 2 or row == 3:
            ws_assumptions.cell(row, 2).number_format = money_format
        else:
            ws_assumptions.cell(row, 2).number_format = percent_format
    autofit(ws_assumptions, 55)

    ws_sensitivity["A1"] = "Sensibilidade — Híbrido 2033 vs. Por Dentro"
    ws_sensitivity["A1"].font = title_font
    ws_sensitivity["A3"] = "Crescimento da receita"
    credit_ratios = [0.50, 0.70, report.base_entradas_credito / report.base_saidas, 1.00]
    for column, ratio in enumerate(credit_ratios, start=2):
        ws_sensitivity.cell(3, column, ratio).number_format = percent_format
    growth_rates = [-0.10, 0.0, 0.10, 0.25]
    for row, growth in enumerate(growth_rates, start=4):
        ws_sensitivity.cell(row, 1, growth).number_format = percent_format
        for column in range(2, 6):
            # Diferença: híbrido (DAS residual + IVA líquido) menos Por Dentro.
            ws_sensitivity.cell(row, column).value = (
                f"=Premissas!$B$2*(1+$A{row})*Premissas!$B$5+"
                f"MAX(Premissas!$B$2*(1+$A{row})*(Premissas!$B$6+Premissas!$B$7)-"
                f"Premissas!$B$2*(1+$A{row})*{get_column_letter(column)}$3*(Premissas!$B$6+Premissas!$B$7),0)-"
                f"Premissas!$B$2*(1+$A{row})*Premissas!$B$4"
            )
            ws_sensitivity.cell(row, column).number_format = money_format
    style_header(ws_sensitivity, 3, 1, 5)
    ws_sensitivity.conditional_formatting.add(
        "B4:E7",
        ColorScaleRule(start_type="min", start_color="63BE7B", mid_type="percentile", mid_value=50, mid_color="FFEB84", end_type="max", end_color="F8696B"),
    )
    ws_sensitivity["A10"] = "Valores negativos favorecem o híbrido; positivos favorecem o Por Dentro, antes dos efeitos comerciais."
    ws_sensitivity["A10"].font = subtitle_font
    autofit(ws_sensitivity, 38)

    ws_monthly.append(["Competência", "Entradas", "Saídas", "Serviços", "Margem bruta de fluxo"])
    for row in monthly.movimentos.itertuples(index=False):
        ws_monthly.append([row[0].to_pydatetime(), row[1], row[2], row[3], row[2] + row[3] - row[1]])
    style_header(ws_monthly, 1, 1, 5)
    for row in range(2, ws_monthly.max_row + 1):
        ws_monthly.cell(row, 1).number_format = "mmm/yyyy"
        for column in range(2, 6):
            ws_monthly.cell(row, column).number_format = money_format
    monthly_table = Table(displayName="TabelaMensal", ref=f"A1:E{ws_monthly.max_row}")
    monthly_table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium4", showRowStripes=True)
    ws_monthly.add_table(monthly_table)
    line_chart = LineChart()
    line_chart.title = "Entradas x Saídas"
    line_chart.y_axis.title = "R$"
    line_chart.x_axis.title = "Competência"
    line_chart.add_data(Reference(ws_monthly, min_col=2, max_col=3, min_row=1, max_row=ws_monthly.max_row), titles_from_data=True)
    line_chart.set_categories(Reference(ws_monthly, min_col=1, min_row=2, max_row=ws_monthly.max_row))
    line_chart.height, line_chart.width = 9, 18
    ws_monthly.add_chart(line_chart, "G2")
    ws_monthly.freeze_panes = "A2"
    autofit(ws_monthly)

    ws_composition.append(["Componente", "Atual", "Híbrido 2027", "Híbrido 2033"])
    components = ["Simples/DAS residual", "CBS", "IBS", "Total"]
    current_total = report.tributos_atuais["Total"]
    values = [
        [current_total, report.fase_2027["simples_residual"], report.fase_2033["simples_residual"]],
        [0.0, report.fase_2027["cbs"], report.fase_2033["cbs"]],
        [0.0, report.fase_2027["ibs"], report.fase_2033["ibs"]],
        [current_total, report.fase_2027["total"], report.fase_2033["total"]],
    ]
    for component, row_values in zip(components, values):
        ws_composition.append([component, *row_values])
    style_header(ws_composition, 1, 1, 4)
    for row in range(2, ws_composition.max_row + 1):
        for column in range(2, 5):
            ws_composition.cell(row, column).number_format = money_format
    chart = BarChart()
    chart.type = "col"
    chart.style = 10
    chart.title = "Composição da carga"
    chart.add_data(Reference(ws_composition, min_col=2, max_col=4, min_row=1, max_row=4), titles_from_data=True)
    chart.set_categories(Reference(ws_composition, min_col=1, min_row=2, max_row=4))
    chart.height, chart.width = 9, 17
    ws_composition.add_chart(chart, "F2")
    autofit(ws_composition)

    ws_attention.append(["Prioridade", "Tema", "Constatação", "Ação recomendada"])
    for point in points:
        ws_attention.append([point["Prioridade"], point["Tema"], point["Constatação"], point["Ação"]])
    style_header(ws_attention, 1, 1, 4)
    for row in range(2, ws_attention.max_row + 1):
        priority = ws_attention.cell(row, 1).value
        fill = red_fill if priority == "CRÍTICO" else yellow_fill if priority == "ATENÇÃO" else blue_fill
        ws_attention.cell(row, 1).fill = fill
        ws_attention.cell(row, 1).font = Font(bold=True)
        for column in range(1, 5):
            ws_attention.cell(row, column).alignment = Alignment(wrap_text=True, vertical="top")
        ws_attention.row_dimensions[row].height = 48
    ws_attention.column_dimensions["A"].width = 14
    ws_attention.column_dimensions["B"].width = 28
    ws_attention.column_dimensions["C"].width = 55
    ws_attention.column_dimensions["D"].width = 75
    ws_attention.freeze_panes = "A2"

    raw_sections = [
        ("Saídas por acumulador", report.saidas_por_acumulador),
        ("Entradas por acumulador", report.entradas_por_acumulador),
        ("Clientes por regime", report.clientes_por_regime),
        ("Fornecedores por regime", report.fornecedores_por_regime),
    ]
    current_row = 1
    for title, frame in raw_sections:
        ws_raw.cell(current_row, 1, title).font = Font(size=14, bold=True, color="123B5D")
        current_row += 1
        for column, header in enumerate(frame.columns, start=1):
            ws_raw.cell(current_row, column, header)
        style_header(ws_raw, current_row, 1, max(len(frame.columns), 1))
        for values in frame.itertuples(index=False, name=None):
            current_row += 1
            for column, value in enumerate(values, start=1):
                ws_raw.cell(current_row, column, value)
                if isinstance(value, float):
                    ws_raw.cell(current_row, column).number_format = money_format
        current_row += 3
    autofit(ws_raw, 65)

    if pgdas is not None:
        compatible = cnpjs_are_compatible(report.cnpj, pgdas)
        ws_pgdas.append(["PGDAS-D importado", "Valor"])
        pgdas_rows = [
            ("Empresa", pgdas.empresa),
            ("CNPJ", pgdas.cnpj_estabelecimento or pgdas.cnpj_basico),
            ("Compatível com Domínio", "SIM" if compatible else "NÃO — arquivo de exemplo separado"),
            ("Período", pgdas.periodo),
            ("Anexo", pgdas.anexo),
            ("RPA", pgdas.rpa),
            ("RBT12", pgdas.rbt12),
            ("DAS", pgdas.total_das),
            ("Alíquota efetiva", pgdas.aliquota_efetiva),
            ("Atividade", pgdas.atividade),
        ]
        for row in pgdas_rows:
            ws_pgdas.append(row)
        style_header(ws_pgdas, 1, 1, 2)
        for row in range(2, ws_pgdas.max_row + 1):
            if ws_pgdas.cell(row, 1).value in {"RPA", "RBT12", "DAS"}:
                ws_pgdas.cell(row, 2).number_format = money_format
            if ws_pgdas.cell(row, 1).value == "Alíquota efetiva":
                ws_pgdas.cell(row, 2).number_format = percent_format
        if not compatible:
            ws_pgdas["B4"].fill = red_fill
            ws_pgdas["B4"].font = Font(bold=True, color="9C0006")
        autofit(ws_pgdas, 100)

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def build_excel_dashboard(
    report: DominioSimulationReport,
    monthly: MonthlyReport,
    pgdas: PGDASReport | None = None,
    projection: FutureProjection | None = None,
    intelligent_report: str | None = None,
    reports: Sequence[DominioSimulationReport] | None = None,
) -> bytes:
    """Cria um XLSX autocontido usando XlsxWriter, com fórmulas e gráficos."""
    import xlsxwriter

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    workbook.set_properties(
        {
            "title": "Dashboard Reforma Tributária IBS/CBS",
            "subject": "Simulação gerencial para optante do Simples Nacional",
            "author": "Portal de Simulação IBS/CBS",
            "comments": "Premissas devem ser validadas conforme legislação e etapa de transição.",
        }
    )
    workbook.set_calc_mode("auto")

    title = workbook.add_format({"bold": True, "font_size": 22, "font_color": "#123B5D"})
    section = workbook.add_format({"bold": True, "font_size": 15, "font_color": "#123B5D"})
    subtitle = workbook.add_format({"font_size": 11, "font_color": "#526579"})
    header = workbook.add_format(
        {"bold": True, "font_color": "white", "bg_color": "#123B5D", "align": "center", "valign": "vcenter", "border": 1, "border_color": "#D7E0E8"}
    )
    text = workbook.add_format({"border": 1, "border_color": "#D7E0E8", "valign": "top"})
    wrap = workbook.add_format({"border": 1, "border_color": "#D7E0E8", "valign": "top", "text_wrap": True})
    money = workbook.add_format({"num_format": 'R$ #,##0.00;[Red]-R$ #,##0.00', "border": 1, "border_color": "#D7E0E8"})
    percent = workbook.add_format({"num_format": "0.00%", "border": 1, "border_color": "#D7E0E8"})
    date_format = workbook.add_format({"num_format": "mmm/yyyy", "border": 1, "border_color": "#D7E0E8"})
    input_percent = workbook.add_format({"num_format": "0.00%", "bg_color": "#FFF4CC", "border": 1, "border_color": "#D7E0E8"})
    note = workbook.add_format({"font_color": "#526579", "italic": True, "text_wrap": True})
    recommendation_format = workbook.add_format(
        {"bg_color": "#DFF3EC", "font_color": "#123B5D", "bold": True, "text_wrap": True, "valign": "top", "border": 1, "border_color": "#16A085"}
    )
    critical = workbook.add_format({"bg_color": "#FDE2E0", "font_color": "#9C0006", "bold": True, "border": 1})
    warning = workbook.add_format({"bg_color": "#FFF4CC", "font_color": "#9C6500", "bold": True, "border": 1})
    decision = workbook.add_format({"bg_color": "#DCEAF7", "font_color": "#123B5D", "bold": True, "border": 1})

    reports = list(reports or [report])
    projection = projection or build_future_projection(reports, monthly)
    intelligent_report = intelligent_report or generate_local_intelligent_report(
        projection, reports, monthly, pgdas
    )
    scenarios = scenario_table(report)
    points = attention_points(report, monthly, pgdas)
    images = create_dashboard_images(report, monthly, pgdas, projection)

    dashboard = workbook.add_worksheet("Dashboard")
    dashboard.hide_gridlines(2)
    dashboard.set_landscape()
    dashboard.set_paper(9)
    dashboard.fit_to_pages(1, 2)
    dashboard.set_margins(0.3, 0.3, 0.4, 0.4)
    dashboard.set_column("A:L", 13)
    dashboard.merge_range("A1:L2", "Dashboard Executivo — Reforma Tributária IBS/CBS", title)
    dashboard.merge_range(
        "A3:L3", f"{report.empresa} | CNPJ {report.cnpj} | Competência {report.periodo:%m/%Y}", subtitle
    )

    kpi_formats = []
    for color in ("#DCEAF7", "#DFF3EC", "#FFF4CC", "#FDE2E0" if report.fase_2027["diferenca"] > 0 else "#DFF3EC"):
        kpi_formats.append(
            workbook.add_format(
                {"bg_color": color, "font_color": "#123B5D", "bold": True, "font_size": 12, "align": "center", "valign": "vcenter", "text_wrap": True, "border": 1, "border_color": "#D7E0E8"}
            )
        )
    dashboard.set_row(4, 52)
    dashboard.merge_range("A5:C5", f"Saídas analisadas\nR$ {report.base_saidas:,.2f}", kpi_formats[0])
    dashboard.merge_range("D5:F5", f"Base de entradas\nR$ {report.base_entradas_credito:,.2f}", kpi_formats[1])
    dashboard.merge_range("G5:I5", f"Operações potencialmente creditáveis\n{report.percentual_operacoes_creditaveis:.2%}", kpi_formats[2])
    dashboard.merge_range("J5:L5", f"Diferença 2027\n{report.fase_2027['diferenca_percentual']:.2%}", kpi_formats[3])
    dashboard.insert_image("A8", "dashboard_cenarios.png", {"image_data": io.BytesIO(images["dashboard_cenarios.png"]), "x_scale": 0.62, "y_scale": 0.62})
    dashboard.write("A32", "Recomendação executiva", section)
    recommendation = (
        "Priorizar a análise do regime híbrido: o relatório do Domínio indica variação de "
        f"{report.fase_2027['diferenca_percentual']:.2%} em 2027 e {report.fase_2033['diferenca_percentual']:.2%} em 2033, "
        f"enquanto aproximadamente {report.percentual_operacoes_creditaveis:.2%} das saídas podem ser sensíveis a crédito. "
        "Condicionar a decisão à conciliação das entradas, validação por cliente e confirmação das alíquotas aplicáveis."
    )
    dashboard.merge_range("A33:L36", recommendation, recommendation_format)
    dashboard.write("A38", "Projeção dos períodos futuros", section)
    dashboard.insert_image(
        "A40",
        "dashboard_projecao_futura.png",
        {"image_data": io.BytesIO(images["dashboard_projecao_futura.png"]), "x_scale": 0.62, "y_scale": 0.62},
    )

    scenario_sheet = workbook.add_worksheet("Cenarios")
    scenario_sheet.freeze_panes(1, 0)
    scenario_sheet.set_column("A:A", 32)
    scenario_sheet.set_column("B:E", 21)
    scenario_sheet.set_column("F:F", 68)
    columns = [
        {"header": "Cenário"}, {"header": "Carga Tributária", "format": money},
        {"header": "Carga Efetiva", "format": percent}, {"header": "Crédito Potencial ao Cliente", "format": money},
        {"header": "Variação vs. Atual", "format": money}, {"header": "Leitura"},
    ]
    scenario_sheet.add_table(
        0, 0, len(scenarios), len(columns) - 1,
        {"name": "TabelaCenarios", "style": "Table Style Medium 2", "columns": columns, "data": scenarios.values.tolist()},
    )
    chart = workbook.add_chart({"type": "column"})
    chart.add_series(
        {"name": "Carga tributária", "categories": "=Cenarios!$A$2:$A$5", "values": "=Cenarios!$B$2:$B$5", "fill": {"color": "#2F80ED"}, "data_labels": {"value": True, "num_format": "R$ #,##0"}}
    )
    chart.set_title({"name": "Carga tributária por cenário"})
    chart.set_y_axis({"name": "R$", "num_format": "R$ #,##0"})
    chart.set_legend({"none": True})
    chart.set_size({"width": 820, "height": 420})
    scenario_sheet.insert_chart("A8", chart)

    assumptions_sheet = workbook.add_worksheet("Premissas")
    assumptions_sheet.set_column("A:A", 34)
    assumptions_sheet.set_column("B:B", 20)
    assumptions_sheet.set_column("C:C", 58)
    assumptions = [
        ["Receita base", report.base_saidas, "Domínio — débitos pelas saídas"],
        ["Base de crédito", report.base_entradas_credito, "Domínio — créditos pelas entradas"],
        ["Alíquota efetiva atual", report.tributos_atuais["Total"] / report.base_saidas, "Carga atual / saídas"],
        ["DAS residual 2033", report.fase_2033["simples_residual"] / report.base_saidas, "Domínio"],
        ["CBS 2033", report.aliquota_cbs_2033, "Domínio"],
        ["IBS 2033", report.aliquota_ibs_2033, "Domínio"],
        ["Receita de contratos em risco", 0.05, "Premissa editável"],
        ["Margem de contribuição", 0.30, "Premissa editável"],
        ["Modo de crescimento", "Automático pela média" if projection.modo_crescimento == "average" else "Percentual fixo", "Projeção futura"],
        ["Crescimento anual aplicado", projection.crescimento_anual, "Calculado pelo motor de projeção"],
        ["Crescimento mensal médio", projection.crescimento_mensal_medio, f"Últimos {projection.meses_calculo_crescimento} períodos"],
    ]
    assumptions_sheet.write_row(0, 0, ["Premissa", "Valor", "Fonte/observação"], header)
    for row_index, row in enumerate(assumptions, start=1):
        assumptions_sheet.write(row_index, 0, row[0], text)
        if row[0] in {"Receita base", "Base de crédito"}:
            fmt = money
        elif row[0] in {"Receita de contratos em risco", "Margem de contribuição"}:
            fmt = input_percent
        elif isinstance(row[1], (int, float)):
            fmt = percent
        else:
            fmt = text
        assumptions_sheet.write(row_index, 1, row[1], fmt)
        assumptions_sheet.write(row_index, 2, row[2], wrap)
    assumptions_sheet.data_validation("B8:B9", {"validate": "decimal", "criteria": "between", "minimum": 0, "maximum": 1, "input_title": "Premissa editável", "input_message": "Informe um percentual entre 0% e 100%."})

    sensitivity = workbook.add_worksheet("Sensibilidade")
    sensitivity.set_column("A:A", 24)
    sensitivity.set_column("B:E", 21)
    sensitivity.merge_range("A1:E1", "Sensibilidade — Híbrido 2033 vs. Por Dentro", title)
    sensitivity.write("A3", "Crescimento da receita", header)
    credit_ratios = [0.50, 0.70, report.base_entradas_credito / report.base_saidas, 1.00]
    for column, ratio in enumerate(credit_ratios, start=1):
        sensitivity.write(2, column, ratio, header)
        sensitivity.set_column(column, column, 20)
    growth_rates = [-0.10, 0.0, 0.10, 0.25]
    for row, growth in enumerate(growth_rates, start=3):
        sensitivity.write(row, 0, growth, percent)
        for column in range(1, 5):
            column_letter = xlsxwriter.utility.xl_col_to_name(column)
            formula = (
                f"=Premissas!$B$2*(1+$A{row + 1})*Premissas!$B$5+"
                f"MAX(Premissas!$B$2*(1+$A{row + 1})*(Premissas!$B$6+Premissas!$B$7)-"
                f"Premissas!$B$2*(1+$A{row + 1})*{column_letter}$3*(Premissas!$B$6+Premissas!$B$7),0)-"
                f"Premissas!$B$2*(1+$A{row + 1})*Premissas!$B$4"
            )
            sensitivity.write_formula(row, column, formula, money, 0)
    sensitivity.conditional_format("B4:E7", {"type": "3_color_scale", "min_color": "#63BE7B", "mid_color": "#FFEB84", "max_color": "#F8696B"})
    sensitivity.merge_range("A10:E11", "Valores negativos favorecem o híbrido; positivos favorecem o Por Dentro, antes dos efeitos comerciais.", note)

    projection_sheet = workbook.add_worksheet("Projecao_Futura")
    projection_sheet.freeze_panes(1, 0)
    projection_sheet.set_column("A:A", 16)
    projection_sheet.set_column("B:J", 21)
    projection_columns = list(projection.projecao_mensal.columns)
    projection_sheet.write_row(0, 0, projection_columns, header)
    for row_index, values in enumerate(
        projection.projecao_mensal.itertuples(index=False, name=None), start=1
    ):
        for column, value in enumerate(values):
            projection_sheet.write(
                row_index,
                column,
                value.to_pydatetime() if column == 0 else value,
                date_format if column == 0 else money,
            )
    projection_sheet.add_table(
        0,
        0,
        len(projection.projecao_mensal),
        len(projection_columns) - 1,
        {
            "name": "TabelaProjecaoFutura",
            "style": "Table Style Medium 9",
            "columns": [{"header": column} for column in projection_columns],
        },
    )
    projection_chart = workbook.add_chart({"type": "line"})
    for column, color in ((3, "#2F80ED"), (4, "#16A085"), (5, "#F2C94C")):
        projection_chart.add_series(
            {
                "name": ["Projecao_Futura", 0, column],
                "categories": ["Projecao_Futura", 1, 0, len(projection.projecao_mensal), 0],
                "values": ["Projecao_Futura", 1, column, len(projection.projecao_mensal), column],
                "line": {"color": color, "width": 2.25},
                "marker": {"type": "circle", "size": 4},
            }
        )
    projection_chart.set_title({"name": "Carga projetada por opção"})
    projection_chart.set_y_axis({"num_format": "R$ #,##0"})
    projection_chart.set_size({"width": 900, "height": 420})
    projection_sheet.insert_chart("L2", projection_chart)

    history_sheet = workbook.add_worksheet("Historico_Simulacoes")
    history_sheet.freeze_panes(1, 0)
    history_sheet.set_column("A:A", 16)
    history_sheet.set_column("B:G", 22)
    history_columns = list(projection.historico_simulacoes.columns)
    history_sheet.write_row(0, 0, history_columns, header)
    for row_index, values in enumerate(
        projection.historico_simulacoes.itertuples(index=False, name=None), start=1
    ):
        for column, value in enumerate(values):
            fmt = date_format if column == 0 else percent if history_columns[column] == "Operações Creditáveis" else money
            history_sheet.write(row_index, column, value.to_pydatetime() if column == 0 else value, fmt)

    ai_sheet = workbook.add_worksheet("Relatorio_Inteligente")
    ai_sheet.hide_gridlines(2)
    ai_sheet.set_column("A:H", 16)
    ai_sheet.merge_range("A1:H2", "Relatório Inteligente de Possibilidades", title)
    ai_sheet.merge_range("A4:H45", intelligent_report, workbook.add_format({"text_wrap": True, "valign": "top", "font_size": 11, "border": 1, "border_color": "#D7E0E8"}))

    monthly_sheet = workbook.add_worksheet("Evolucao_Mensal")
    monthly_sheet.freeze_panes(1, 0)
    monthly_sheet.set_column("A:A", 16)
    monthly_sheet.set_column("B:E", 18)
    monthly_sheet.add_table(
        0, 0, len(monthly.movimentos), 4,
        {
            "name": "TabelaMensal", "style": "Table Style Medium 4",
            "columns": [{"header": "Competência", "format": date_format}, {"header": "Entradas", "format": money}, {"header": "Saídas", "format": money}, {"header": "Serviços", "format": money}, {"header": "Margem bruta de fluxo", "format": money}],
            "data": [[row[0].to_pydatetime(), row[1], row[2], row[3], row[2] + row[3] - row[1]] for row in monthly.movimentos.itertuples(index=False)],
        },
    )
    line = workbook.add_chart({"type": "line"})
    for column, color in ((2, "#16A085"), (3, "#2F80ED")):
        line.add_series({"name": ["Evolucao_Mensal", 0, column - 1], "categories": ["Evolucao_Mensal", 1, 0, len(monthly.movimentos), 0], "values": ["Evolucao_Mensal", 1, column - 1, len(monthly.movimentos), column - 1], "line": {"color": color, "width": 2.25}, "marker": {"type": "circle", "size": 5}})
    line.set_title({"name": "Evolução mensal de entradas e saídas"})
    line.set_y_axis({"num_format": "R$ #,##0"})
    line.set_size({"width": 900, "height": 420})
    monthly_sheet.insert_chart("G2", line)

    composition = workbook.add_worksheet("Composicao")
    composition.set_column("A:A", 28)
    composition.set_column("B:D", 20)
    composition.write_row(0, 0, ["Componente", "Atual", "Híbrido 2027", "Híbrido 2033"], header)
    current_total = report.tributos_atuais["Total"]
    composition_rows = [
        ["Simples/DAS residual", current_total, report.fase_2027["simples_residual"], report.fase_2033["simples_residual"]],
        ["CBS", 0, report.fase_2027["cbs"], report.fase_2033["cbs"]],
        ["IBS", 0, report.fase_2027["ibs"], report.fase_2033["ibs"]],
        ["Total", current_total, report.fase_2027["total"], report.fase_2033["total"]],
    ]
    for row_index, row in enumerate(composition_rows, start=1):
        composition.write(row_index, 0, row[0], text)
        composition.write_row(row_index, 1, row[1:], money)
    comp_chart = workbook.add_chart({"type": "column", "subtype": "stacked"})
    for column in range(1, 4):
        comp_chart.add_series({"name": ["Composicao", 0, column], "categories": "=Composicao!$A$2:$A$4", "values": ["Composicao", 1, column, 3, column]})
    comp_chart.set_title({"name": "Composição por cenário"})
    comp_chart.set_y_axis({"num_format": "R$ #,##0"})
    comp_chart.set_size({"width": 780, "height": 420})
    composition.insert_chart("F2", comp_chart)

    attention = workbook.add_worksheet("Pontos_Atencao")
    attention.freeze_panes(1, 0)
    attention.set_column("A:A", 14)
    attention.set_column("B:B", 28)
    attention.set_column("C:C", 55)
    attention.set_column("D:D", 75)
    attention.write_row(0, 0, ["Prioridade", "Tema", "Constatação", "Ação recomendada"], header)
    priority_formats = {"CRÍTICO": critical, "ATENÇÃO": warning, "DECISÃO": decision}
    for row_index, point in enumerate(points, start=1):
        attention.set_row(row_index, 46)
        attention.write(row_index, 0, point["Prioridade"], priority_formats.get(point["Prioridade"], text))
        attention.write(row_index, 1, point["Tema"], wrap)
        attention.write(row_index, 2, point["Constatação"], wrap)
        attention.write(row_index, 3, point["Ação"], wrap)

    raw = workbook.add_worksheet("Dados_Dominio")
    raw.set_column("A:A", 62)
    raw.set_column("B:C", 20)
    current_row = 0
    for section_name, frame in (
        ("Saídas por acumulador", report.saidas_por_acumulador),
        ("Entradas por acumulador", report.entradas_por_acumulador),
        ("Clientes por regime", report.clientes_por_regime),
        ("Fornecedores por regime", report.fornecedores_por_regime),
    ):
        raw.write(current_row, 0, section_name, section)
        current_row += 1
        raw.write_row(current_row, 0, list(frame.columns), header)
        current_row += 1
        for values in frame.itertuples(index=False, name=None):
            for column, value in enumerate(values):
                fmt = percent if frame.columns[column] == "Percentual" else money if isinstance(value, float) else text
                raw.write(current_row, column, value, fmt)
            current_row += 1
        current_row += 2

    if pgdas is not None:
        pgdas_sheet = workbook.add_worksheet("PGDAS_Exemplo")
        pgdas_sheet.set_column("A:A", 30)
        pgdas_sheet.set_column("B:B", 95)
        compatible = cnpjs_are_compatible(report.cnpj, pgdas)
        pgdas_sheet.write_row(0, 0, ["PGDAS-D importado", "Valor"], header)
        pgdas_rows = [
            ["Empresa", pgdas.empresa], ["CNPJ", pgdas.cnpj_estabelecimento or pgdas.cnpj_basico],
            ["Compatível com Domínio", "SIM" if compatible else "NÃO — arquivo de exemplo separado"],
            ["Período", pgdas.periodo], ["Anexo", pgdas.anexo], ["RPA", pgdas.rpa], ["RBT12", pgdas.rbt12],
            ["DAS", pgdas.total_das], ["Alíquota efetiva", pgdas.aliquota_efetiva], ["Atividade", pgdas.atividade],
        ]
        for row_index, row in enumerate(pgdas_rows, start=1):
            pgdas_sheet.write(row_index, 0, row[0], text)
            if row[0] in {"RPA", "RBT12", "DAS"}:
                fmt = money
            elif row[0] == "Alíquota efetiva":
                fmt = percent
            elif row[0] == "Compatível com Domínio" and not compatible:
                fmt = critical
            else:
                fmt = wrap
            pgdas_sheet.write(row_index, 1, row[1], fmt)

    workbook.close()
    return output.getvalue()


def build_transactional_template() -> bytes:
    """Modelo XLSX com as três abas aceitas pelo modo transacional."""
    import xlsxwriter

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    title = workbook.add_format({"bold": True, "font_size": 18, "font_color": "#123B5D"})
    header = workbook.add_format({"bold": True, "font_color": "white", "bg_color": "#123B5D", "border": 1, "align": "center"})
    input_format = workbook.add_format({"bg_color": "#FFF4CC", "border": 1})
    date_format = workbook.add_format({"num_format": "dd/mm/yyyy", "bg_color": "#FFF4CC", "border": 1})
    money_format = workbook.add_format({"num_format": 'R$ #,##0.00', "bg_color": "#FFF4CC", "border": 1})
    percent_format = workbook.add_format({"num_format": "0.00%", "bg_color": "#FFF4CC", "border": 1})
    note = workbook.add_format({"text_wrap": True, "valign": "top", "font_color": "#526579"})

    instructions = workbook.add_worksheet("Instrucoes")
    instructions.set_column("A:A", 28)
    instructions.set_column("B:B", 100)
    instructions.merge_range("A1:B2", "Modelo de Importação — Portal IBS/CBS", title)
    instruction_rows = [
        ("Faturamento", "Uma linha por documento/operação. Preserve zeros à esquerda no CPF/CNPJ."),
        ("Entradas", "Informe a base potencial de crédito já conciliada; o portal ainda sinalizará a necessidade de validação fiscal."),
        ("Parametros_SN", "Use apenas uma linha válida. A alíquota pode ser informada como 8,50% ou 0,085."),
        ("Datas", "Utilize datas reais do Excel ou o formato dd/mm/aaaa."),
        ("Valores", "Utilize números, sem fórmulas externas. Valores negativos serão tratados como devoluções/ajustes."),
        ("Cabeçalhos", "Não altere os nomes das colunas. Não inclua títulos antes da primeira linha."),
    ]
    instructions.write_row(3, 0, ["Aba", "Orientação"], header)
    for row_index, row in enumerate(instruction_rows, start=4):
        instructions.write(row_index, 0, row[0], input_format)
        instructions.write(row_index, 1, row[1], note)

    sales = workbook.add_worksheet("Faturamento")
    sales.set_column("A:A", 15)
    sales.set_column("B:B", 12)
    sales.set_column("C:C", 24)
    sales.set_column("D:D", 18)
    sales.write_row(0, 0, ["Data", "CFOP", "CNPJ_CPF_Cliente", "Valor_Total"], header)
    sales.write_datetime(1, 0, pd.Timestamp("2026-05-02").to_pydatetime(), date_format)
    sales.write(1, 1, "5102", input_format)
    sales.write(1, 2, "12.345.678/0001-90", input_format)
    sales.write_number(1, 3, 10000.00, money_format)
    sales.autofilter(0, 0, 1000, 3)
    sales.freeze_panes(1, 0)

    purchases = workbook.add_worksheet("Entradas")
    purchases.set_column("A:A", 15)
    purchases.set_column("B:B", 12)
    purchases.set_column("C:D", 22)
    purchases.write_row(0, 0, ["Data", "CFOP", "Valor_Total_Nota", "Valor_Base_Credito"], header)
    purchases.write_datetime(1, 0, pd.Timestamp("2026-05-03").to_pydatetime(), date_format)
    purchases.write(1, 1, "1102", input_format)
    purchases.write_number(1, 2, 7500.00, money_format)
    purchases.write_number(1, 3, 7000.00, money_format)
    purchases.autofilter(0, 0, 1000, 3)
    purchases.freeze_panes(1, 0)

    parameters = workbook.add_worksheet("Parametros_SN")
    parameters.set_column("A:A", 20)
    parameters.set_column("B:B", 14)
    parameters.set_column("C:C", 26)
    parameters.write_row(0, 0, ["RBT12", "Anexo", "Aliquota_Efetiva_Atual"], header)
    parameters.write_number(1, 0, 1200000.00, money_format)
    parameters.write(1, 1, "I", input_format)
    parameters.write_number(1, 2, 0.085, percent_format)
    parameters.data_validation(1, 1, 100, 1, {"validate": "list", "source": ["I", "II", "III", "IV", "V"]})
    parameters.data_validation(1, 2, 100, 2, {"validate": "decimal", "criteria": "between", "minimum": 0, "maximum": 1})
    parameters.freeze_panes(1, 0)

    workbook.close()
    return output.getvalue()
