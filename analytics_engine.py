"""Projeções financeiras e relatório inteligente para apoio à decisão."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import pandas as pd

from dominio_importers import DominioSimulationReport, MonthlyReport, PGDASReport, cnpjs_are_compatible


class AnalyticsError(ValueError):
    """Inconsistência que impede a projeção consolidada."""


@dataclass(frozen=True)
class FutureProjection:
    meses_media: int
    horizonte_meses: int
    modo_crescimento: str
    crescimento_anual: float
    crescimento_mensal_medio: float
    meses_calculo_crescimento: int
    media_saidas: float
    media_entradas: float
    aliquota_atual_media: float
    aliquota_hibrida_2027: float
    aliquota_hibrida_2033: float
    percentual_operacoes_creditaveis: float
    volatilidade_saidas: float
    tendencia_recente: float
    historico_simulacoes: pd.DataFrame = field(compare=False)
    projecao_mensal: pd.DataFrame = field(compare=False)

    @property
    def totais(self) -> dict[str, float]:
        return {
            "receita": float(self.projecao_mensal["Saídas Projetadas"].sum()),
            "entradas": float(self.projecao_mensal["Entradas Projetadas"].sum()),
            "por_dentro": float(self.projecao_mensal["Por Dentro"].sum()),
            "hibrido_2027": float(self.projecao_mensal["Híbrido 2027"].sum()),
            "hibrido_2033": float(self.projecao_mensal["Híbrido 2033"].sum()),
            "credito_por_dentro": float(self.projecao_mensal["Crédito Por Dentro"].sum()),
            "credito_hibrido_2027": float(self.projecao_mensal["Crédito Híbrido 2027"].sum()),
            "credito_hibrido_2033": float(self.projecao_mensal["Crédito Híbrido 2033"].sum()),
        }


def _deduplicate_reports(
    reports: Sequence[DominioSimulationReport],
) -> list[DominioSimulationReport]:
    if not reports:
        raise AnalyticsError("Nenhum relatório de simulação foi informado.")
    cnpjs = {report.cnpj for report in reports}
    if len(cnpjs) > 1:
        raise AnalyticsError(
            "Os arquivos de Simulação da Reforma pertencem a CNPJs diferentes e não podem ser consolidados."
        )
    # Se a mesma competência for enviada novamente, prevalece o último arquivo.
    by_period = {report.periodo.to_period("M"): report for report in reports}
    return sorted(by_period.values(), key=lambda report: report.periodo)


def build_future_projection(
    reports: Sequence[DominioSimulationReport],
    monthly: MonthlyReport,
    horizon_months: int = 12,
    average_months: int = 12,
    annual_growth: float = 0.0,
    growth_mode: str = "fixed",
    growth_lookback_months: int = 6,
) -> FutureProjection:
    reports = _deduplicate_reports(reports)
    if reports[0].cnpj != monthly.cnpj:
        raise AnalyticsError("O Demonstrativo Mensal e as simulações pertencem a CNPJs diferentes.")
    if not 1 <= horizon_months <= 60:
        raise AnalyticsError("O horizonte deve estar entre 1 e 60 meses.")
    if not 1 <= average_months <= len(monthly.movimentos):
        average_months = min(max(average_months, 1), len(monthly.movimentos))
    if annual_growth <= -1:
        raise AnalyticsError("O crescimento anual precisa ser superior a -100%.")

    normalized_mode = growth_mode.strip().lower()
    if normalized_mode not in {"fixed", "average"}:
        raise AnalyticsError("Modo de crescimento inválido. Use 'fixed' ou 'average'.")

    all_movements = monthly.movimentos.sort_values("Competência").copy()
    movements = all_movements.tail(average_months).copy()
    total_sales_history = movements["Saídas"] + movements["Serviços"]
    average_sales = float(total_sales_history.mean())
    average_inputs = float(movements["Entradas"].mean())
    volatility = float(total_sales_history.std(ddof=0) / average_sales) if average_sales else 0.0
    recent = float(total_sales_history.tail(min(3, len(total_sales_history))).mean())
    previous_slice = total_sales_history.iloc[-6:-3]
    previous = float(previous_slice.mean()) if not previous_slice.empty else average_sales
    recent_trend = recent / previous - 1 if previous else 0.0

    all_sales = all_movements["Saídas"] + all_movements["Serviços"]
    available_growth_periods = max(len(all_sales) - 1, 1)
    growth_lookback_months = min(max(growth_lookback_months, 1), available_growth_periods)
    monthly_changes = (
        all_sales.pct_change(fill_method=None)
        .replace([float("inf"), float("-inf")], pd.NA)
        .dropna()
        .tail(growth_lookback_months)
    )
    valid_changes = monthly_changes[monthly_changes > -1]
    average_monthly_growth = (
        float((1 + valid_changes).prod() ** (1 / len(valid_changes)) - 1)
        if not valid_changes.empty
        else 0.0
    )
    if normalized_mode == "average":
        applied_annual_growth = (1 + average_monthly_growth) ** 12 - 1
    else:
        applied_annual_growth = annual_growth
        average_monthly_growth = (1 + annual_growth) ** (1 / 12) - 1

    total_base = sum(report.base_saidas for report in reports)
    if total_base <= 0:
        raise AnalyticsError("A soma das bases de saída precisa ser maior que zero.")

    def weighted_rate(get_value) -> float:
        return sum(get_value(report) for report in reports) / total_base

    current_rate = weighted_rate(lambda report: report.tributos_atuais["Total"])
    hybrid_2027_rate = weighted_rate(lambda report: report.fase_2027["total"])
    hybrid_2033_rate = weighted_rate(lambda report: report.fase_2033["total"])
    creditable_ratio = sum(
        report.percentual_operacoes_creditaveis * report.base_saidas for report in reports
    ) / total_base
    cbs_2027_rate = weighted_rate(lambda report: report.base_saidas * report.aliquota_cbs_2027)
    ibs_2027_rate = weighted_rate(lambda report: report.base_saidas * report.aliquota_ibs_2027)
    cbs_2033_rate = weighted_rate(lambda report: report.base_saidas * report.aliquota_cbs_2033)
    ibs_2033_rate = weighted_rate(lambda report: report.base_saidas * report.aliquota_ibs_2033)
    current_tax_total = sum(report.tributos_atuais["Total"] for report in reports)
    current_replaced_share = sum(
        sum(report.tributos_atuais.get(tax, 0.0) for tax in ("ICMS", "ISS", "PIS/Pasep", "COFINS"))
        for report in reports
    ) / current_tax_total if current_tax_total else 0.0

    last_period = monthly.movimentos["Competência"].max().to_period("M")
    projected_rows: list[dict[str, object]] = []
    for month_index in range(1, horizon_months + 1):
        competence = (last_period + month_index).to_timestamp()
        growth_factor = (1 + applied_annual_growth) ** (month_index / 12)
        sales = average_sales * growth_factor
        inputs = average_inputs * growth_factor
        inside = sales * current_rate
        hybrid_2027 = sales * hybrid_2027_rate
        hybrid_2033 = sales * hybrid_2033_rate
        creditable_sales = sales * creditable_ratio
        projected_rows.append(
            {
                "Competência": competence,
                "Saídas Projetadas": sales,
                "Entradas Projetadas": inputs,
                "Por Dentro": inside,
                "Híbrido 2027": hybrid_2027,
                "Híbrido 2033": hybrid_2033,
                "Diferença 2027": hybrid_2027 - inside,
                "Diferença 2033": hybrid_2033 - inside,
                "Crédito Por Dentro": creditable_sales * current_rate * current_replaced_share,
                "Crédito Híbrido 2027": creditable_sales * (cbs_2027_rate + ibs_2027_rate),
                "Crédito Híbrido 2033": creditable_sales * (cbs_2033_rate + ibs_2033_rate),
            }
        )

    history = pd.DataFrame(
        [
            {
                "Competência": report.periodo,
                "Saídas": report.base_saidas,
                "Entradas para Crédito": report.base_entradas_credito,
                "Carga Atual": report.tributos_atuais["Total"],
                "Híbrido 2027": report.fase_2027["total"],
                "Híbrido 2033": report.fase_2033["total"],
                "Operações Creditáveis": report.percentual_operacoes_creditaveis,
            }
            for report in reports
        ]
    )
    return FutureProjection(
        meses_media=average_months,
        horizonte_meses=horizon_months,
        modo_crescimento=normalized_mode,
        crescimento_anual=applied_annual_growth,
        crescimento_mensal_medio=average_monthly_growth,
        meses_calculo_crescimento=growth_lookback_months,
        media_saidas=average_sales,
        media_entradas=average_inputs,
        aliquota_atual_media=current_rate,
        aliquota_hibrida_2027=hybrid_2027_rate,
        aliquota_hibrida_2033=hybrid_2033_rate,
        percentual_operacoes_creditaveis=creditable_ratio,
        volatilidade_saidas=volatility,
        tendencia_recente=recent_trend,
        historico_simulacoes=history,
        projecao_mensal=pd.DataFrame(projected_rows),
    )


def generate_local_intelligent_report(
    projection: FutureProjection,
    reports: Sequence[DominioSimulationReport],
    monthly: MonthlyReport,
    pgdas: PGDASReport | None = None,
) -> str:
    """Relatório analítico local, reproduzível e sem envio de dados externos."""
    def money(value: float) -> str:
        absolute = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"-R$ {absolute}" if value < 0 else f"R$ {absolute}"

    totals = projection.totais
    delta_2027 = totals["hibrido_2027"] - totals["por_dentro"]
    delta_2033 = totals["hibrido_2033"] - totals["por_dentro"]
    input_coverage = projection.media_entradas / projection.media_saidas if projection.media_saidas else 0.0
    trend_label = "crescimento" if projection.tendencia_recente > 0.03 else "queda" if projection.tendencia_recente < -0.03 else "estabilidade"
    preferred = "Híbrido" if projection.percentual_operacoes_creditaveis > 0.60 and delta_2033 <= delta_2027 else "Por Dentro"
    growth_description = (
        f"percentual fixo anual de {projection.crescimento_anual:.2%}"
        if projection.modo_crescimento == "fixed"
        else f"crescimento automático de {projection.crescimento_anual:.2%} ao ano, calculado pela média mensal de {projection.crescimento_mensal_medio:.2%} dos últimos {projection.meses_calculo_crescimento} períodos"
    )
    pgdas_note = "Não foi importado PGDAS da empresa."
    if pgdas:
        pgdas_note = (
            f"PGDAS compatível, Anexo {pgdas.anexo}, alíquota efetiva {pgdas.aliquota_efetiva:.2%}."
            if cnpjs_are_compatible(reports[-1].cnpj, pgdas)
            else "O PGDAS importado pertence a outro CNPJ e foi excluído das conclusões."
        )

    return f"""## Relatório inteligente de possibilidades

### Síntese executiva

A projeção de {projection.horizonte_meses} meses utiliza a média dos últimos {projection.meses_media} meses e {growth_description}. A receita projetada é de {money(totals['receita'])}. O comportamento recente indica **{trend_label}** de {projection.tendencia_recente:.2%}, com volatilidade mensal de {projection.volatilidade_saidas:.2%}.

### Possibilidade 1 — Simples Nacional Por Dentro

- Carga projetada: **{money(totals['por_dentro'])}**.
- Crédito potencial aos clientes: **{money(totals['credito_por_dentro'])}**.
- Adequado quando a simplicidade operacional e o atendimento a consumidores finais predominam.
- Ponto de atenção: clientes sujeitos ao regime regular podem pressionar preços pela menor transferência de créditos.

### Possibilidade 2 — Regime Híbrido em 2027

- Carga projetada: **{money(totals['hibrido_2027'])}**.
- Diferença contra o Por Dentro: **{money(delta_2027)}**.
- Crédito potencial aos clientes: **{money(totals['credito_hibrido_2027'])}**.
- Exige preparação documental, segregação do DAS residual e conciliação dos créditos.

### Possibilidade 3 — Cenário estrutural Híbrido 2033

- Carga projetada: **{money(totals['hibrido_2033'])}**.
- Diferença contra o Por Dentro: **{money(delta_2033)}**.
- Crédito potencial aos clientes: **{money(totals['credito_hibrido_2033'])}**.
- Deve ser revisado quando forem confirmadas as alíquotas do destino e tratamentos diferenciados.

### Recomendação preliminar

Priorizar o estudo do **{preferred}**. As operações potencialmente creditáveis representam {projection.percentual_operacoes_creditaveis:.2%} das saídas e a cobertura média de entradas corresponde a {input_coverage:.2%} das vendas. A recomendação é preliminar e deve ser validada por cliente, produto/serviço, documento fiscal e fluxo financeiro.

### Plano de ação

1. Importar todos os meses disponíveis da Simulação da Reforma para reduzir dependência de uma única competência.
2. Conciliar a base de entradas com documentos efetivamente elegíveis a crédito.
3. Classificar clientes por CPF/CNPJ e regime para medir a dependência B2B real.
4. Simular repasse de preço, margem de contribuição e eventual perda de contratos.
5. Confirmar alíquotas, reduções, regimes específicos e cronograma antes da opção.
6. Preparar cadastro fiscal, documentos eletrônicos e controles de créditos.

### Validação do PGDAS

{pgdas_note}

> Relatório gerencial produzido por motor analítico local. Não substitui parecer tributário ou apuração oficial.
"""


def generate_report_with_ai(
    local_report: str,
    projection: FutureProjection,
    api_key: str,
    model: str,
) -> str:
    """Enriquece o relatório por API de IA, somente após ação explícita do usuário."""
    if not api_key or not model:
        raise AnalyticsError("Informe a chave da API e o modelo para usar IA generativa.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AnalyticsError("A biblioteca openai não está instalada. Execute o arquivo iniciar_portal.bat.") from exc
    prompt = f"""Atue como consultor tributário sênior. Revise o relatório abaixo e produza uma versão executiva em português do Brasil. Preserve os números, não invente regras ou alíquotas, diferencie fatos de premissas e organize: resumo, três possibilidades, riscos, recomendação condicional e plano de ação. Inclua aviso de que não substitui parecer profissional.

Dados estruturados:
- horizonte: {projection.horizonte_meses} meses
- média histórica: {projection.meses_media} meses
- modo de crescimento: {projection.modo_crescimento}
- média mensal calculada: {projection.crescimento_mensal_medio:.4f}
- crescimento anual: {projection.crescimento_anual:.4f}

Relatório-base:
{local_report}
"""
    try:
        response = OpenAI(api_key=api_key).responses.create(model=model, input=prompt)
        return response.output_text
    except Exception as exc:
        raise AnalyticsError(f"A IA generativa não conseguiu produzir o relatório: {exc}") from exc
