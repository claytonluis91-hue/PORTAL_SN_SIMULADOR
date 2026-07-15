"""Projeções financeiras e relatório inteligente para apoio à decisão."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import pandas as pd

from dominio_importers import DominioSimulationReport, MonthlyReport, PGDASReport, cnpjs_are_compatible
from simples_lc214 import SimplesLC214Simulation


class AnalyticsError(ValueError):
    """Inconsistência que impede a projeção consolidada."""


GEMINI_MODEL_PREFERENCE = (
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
)


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
    aliquota_por_dentro: float
    aliquota_hibrida_2027: float
    aliquota_hibrida_2033: float
    percentual_operacoes_creditaveis: float
    percentual_entradas_creditaveis: float
    volatilidade_saidas: float
    tendencia_recente: float
    historico_simulacoes: pd.DataFrame = field(compare=False)
    projecao_mensal: pd.DataFrame = field(compare=False)
    resumo_anual: pd.DataFrame = field(compare=False)

    @property
    def totais(self) -> dict[str, float]:
        return {
            "receita": float(self.projecao_mensal["Saídas Projetadas"].sum()),
            "entradas": float(self.projecao_mensal["Entradas Projetadas"].sum()),
            "por_dentro": float(self.projecao_mensal["Carga · Por Dentro"].sum()),
            "hibrido_2027": float(self.projecao_mensal["Carga · Híbrido 2027"].sum()),
            "hibrido_2033": float(self.projecao_mensal["Carga · Híbrido 2033"].sum()),
            "credito_compras_2027": float(
                self.projecao_mensal["Crédito das Compras · Híbrido 2027"].sum()
            ),
            "credito_compras_2033": float(
                self.projecao_mensal["Crédito das Compras · Híbrido 2033"].sum()
            ),
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
    lc214_simulation: SimplesLC214Simulation | None = None,
) -> FutureProjection:
    reports = _deduplicate_reports(reports)
    if reports[0].cnpj != monthly.cnpj:
        raise AnalyticsError("O Demonstrativo Mensal e as simulações pertencem a CNPJs diferentes.")
    # A apresentação é anual: janeiro a dezembro de 2027. O cenário 2033
    # reutiliza a mesma base anual para isolar o efeito da mudança tributária.
    horizon_months = 12
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
    creditable_ratio = sum(
        report.percentual_operacoes_creditaveis * report.base_saidas for report in reports
    ) / total_base
    residual_2027_rate = weighted_rate(lambda report: report.fase_2027["simples_residual"])
    residual_2033_rate = weighted_rate(lambda report: report.fase_2033["simples_residual"])
    cbs_2027_rate = weighted_rate(lambda report: report.base_saidas * report.aliquota_cbs_2027)
    ibs_2027_rate = weighted_rate(lambda report: report.base_saidas * report.aliquota_ibs_2027)
    cbs_2033_rate = weighted_rate(lambda report: report.base_saidas * report.aliquota_cbs_2033)
    ibs_2033_rate = weighted_rate(lambda report: report.base_saidas * report.aliquota_ibs_2033)
    total_input_base = sum(report.base_entradas_credito for report in reports)

    def weighted_input_rate(attribute: str) -> float:
        if total_input_base <= 0:
            return 0.0
        return sum(
            report.base_entradas_credito * getattr(report, attribute)
            for report in reports
        ) / total_input_base

    input_cbs_2027_rate = weighted_input_rate("aliquota_credito_cbs_2027")
    input_ibs_2027_rate = weighted_input_rate("aliquota_credito_ibs_2027")
    input_cbs_2033_rate = weighted_input_rate("aliquota_credito_cbs_2033")
    input_ibs_2033_rate = weighted_input_rate("aliquota_credito_ibs_2033")
    if lc214_simulation is not None and lc214_simulation.revenue <= 0:
        raise AnalyticsError("A receita da simulação LC 214 precisa ser maior que zero.")

    # No Simples "por dentro", a transição de 2027 altera a repartição
    # interna do DAS, mas não cria, por si só, uma nova carga efetiva para a
    # mesma empresa, mesma base e mesmas segregações. A tabela LC 214 segue
    # disponível para conferir faixa e partilha; a projeção comparativa preserva
    # a alíquota efetiva importada de 2026.
    inside_rate = current_rate

    matched_input_total = 0.0
    for report in reports:
        period_rows = all_movements[
            all_movements["Competência"].dt.to_period("M") == report.periodo.to_period("M")
        ]
        if not period_rows.empty:
            matched_input_total += float(period_rows["Entradas"].sum())
    reported_credit_base = sum(report.base_entradas_credito for report in reports)
    projected_input_ratio = (
        matched_input_total / total_base
        if matched_input_total > 0
        else average_inputs / average_sales
        if average_sales > 0
        else 0.0
    )
    input_creditable_ratio = (
        min(max(reported_credit_base / matched_input_total, 0.0), 1.0)
        if matched_input_total > 0
        else 1.0
    )

    last_period = monthly.movimentos["Competência"].max().to_period("M")
    target_start = pd.Period("2027-01", freq="M")
    months_to_target = (target_start.year - last_period.year) * 12 + (
        target_start.month - last_period.month
    )
    projected_rows: list[dict[str, object]] = []
    for month_index in range(horizon_months):
        competence = (target_start + month_index).to_timestamp()
        months_ahead = months_to_target + month_index
        growth_factor = (1 + applied_annual_growth) ** (months_ahead / 12)
        sales = average_sales * growth_factor
        inputs = sales * projected_input_ratio
        creditable_inputs = inputs * input_creditable_ratio
        inside = sales * inside_rate
        residual_2027 = sales * residual_2027_rate
        residual_2033 = sales * residual_2033_rate
        credit_cbs_2027 = creditable_inputs * input_cbs_2027_rate
        credit_ibs_2027 = creditable_inputs * input_ibs_2027_rate
        credit_cbs_2033 = creditable_inputs * input_cbs_2033_rate
        credit_ibs_2033 = creditable_inputs * input_ibs_2033_rate
        cbs_2027 = max(sales * cbs_2027_rate - credit_cbs_2027, 0.0)
        ibs_2027 = max(sales * ibs_2027_rate - credit_ibs_2027, 0.0)
        cbs_2033 = max(sales * cbs_2033_rate - credit_cbs_2033, 0.0)
        ibs_2033 = max(sales * ibs_2033_rate - credit_ibs_2033, 0.0)
        hybrid_2027 = residual_2027 + cbs_2027 + ibs_2027
        hybrid_2033 = residual_2033 + cbs_2033 + ibs_2033
        projected_rows.append(
            {
                "Competência": competence,
                "Saídas Projetadas": sales,
                "Entradas Projetadas": inputs,
                "Base de Compras Creditável": creditable_inputs,
                "Carga · Por Dentro": inside,
                "DAS Residual · Híbrido 2027": residual_2027,
                "CBS Líquida · Híbrido 2027": cbs_2027,
                "IBS Líquido · Híbrido 2027": ibs_2027,
                "Carga · Híbrido 2027": hybrid_2027,
                "Crédito das Compras · Híbrido 2027": credit_cbs_2027
                + credit_ibs_2027,
                "DAS Residual · Híbrido 2033": residual_2033,
                "CBS Líquida · Híbrido 2033": cbs_2033,
                "IBS Líquido · Híbrido 2033": ibs_2033,
                "Carga · Híbrido 2033": hybrid_2033,
                "Crédito das Compras · Híbrido 2033": credit_cbs_2033
                + credit_ibs_2033,
                "Diferença 2027": hybrid_2027 - inside,
                "Diferença 2033": hybrid_2033 - inside,
            }
        )

    projection_frame = pd.DataFrame(projected_rows)
    annual_sales = float(projection_frame["Saídas Projetadas"].sum())
    annual_inputs = float(projection_frame["Entradas Projetadas"].sum())
    annual_creditable_inputs = float(
        projection_frame["Base de Compras Creditável"].sum()
    )
    annual_inside = float(projection_frame["Carga · Por Dentro"].sum())

    def annual_row(year: int) -> dict[str, object]:
        suffix = str(year)
        residual = float(projection_frame[f"DAS Residual · Híbrido {suffix}"].sum())
        cbs = float(projection_frame[f"CBS Líquida · Híbrido {suffix}"].sum())
        ibs = float(projection_frame[f"IBS Líquido · Híbrido {suffix}"].sum())
        credit = float(
            projection_frame[f"Crédito das Compras · Híbrido {suffix}"].sum()
        )
        hybrid_total = residual + cbs + ibs
        return {
            "Período": "2027 completo" if year == 2027 else "2033 estrutural",
            "Receita Projetada": annual_sales,
            "Compras Projetadas": annual_inputs,
            "Base Creditável das Compras": annual_creditable_inputs,
            "DAS Normal · Alíquota Efetiva": inside_rate,
            "DAS Normal · Valor": annual_inside,
            "Híbrido · Alíquota Efetiva": hybrid_total / annual_sales
            if annual_sales
            else 0.0,
            "Híbrido · DAS Residual": residual,
            "Híbrido · CBS Líquida": cbs,
            "Híbrido · IBS Líquido": ibs,
            "Crédito Estimado das Compras": credit,
            "Híbrido · Total a Pagar": hybrid_total,
            "Diferença vs. DAS Normal": hybrid_total - annual_inside,
        }

    annual_summary = pd.DataFrame([annual_row(2027), annual_row(2033)])
    hybrid_2027_rate = float(
        annual_summary.loc[0, "Híbrido · Alíquota Efetiva"]
    )
    hybrid_2033_rate = float(
        annual_summary.loc[1, "Híbrido · Alíquota Efetiva"]
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
        aliquota_por_dentro=inside_rate,
        aliquota_hibrida_2027=hybrid_2027_rate,
        aliquota_hibrida_2033=hybrid_2033_rate,
        percentual_operacoes_creditaveis=creditable_ratio,
        percentual_entradas_creditaveis=input_creditable_ratio,
        volatilidade_saidas=volatility,
        tendencia_recente=recent_trend,
        historico_simulacoes=history,
        projecao_mensal=projection_frame,
        resumo_anual=annual_summary,
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
    preferred = (
        "Híbrido"
        if projection.percentual_entradas_creditaveis > 0.60 and delta_2033 <= delta_2027
        else "Por Dentro"
    )
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

    return f"""## Relatório Consultivo Nascel — possibilidades tributárias

### Síntese executiva

A projeção cobre janeiro a dezembro de 2027, utiliza a média dos últimos {projection.meses_media} meses e {growth_description}. A receita anual projetada é de {money(totals['receita'])}. O cenário 2033 reutiliza a mesma receita, compras e alíquota efetiva do DAS Normal de 2027 para isolar as premissas tributárias do Híbrido. O comportamento recente indica **{trend_label}** de {projection.tendencia_recente:.2%}, com volatilidade mensal de {projection.volatilidade_saidas:.2%}.

### Possibilidade 1 — Simples Nacional Por Dentro

- Carga projetada: **{money(totals['por_dentro'])}**.
- Alíquota efetiva projetada: **{projection.aliquota_por_dentro:.2%}**.
- Crédito de IBS/CBS sobre as compras: **R$ 0,00**, pois os tributos permanecem dentro do DAS.
- Adequado quando a simplicidade operacional e o atendimento a consumidores finais predominam.
- Ponto de atenção: clientes sujeitos ao regime regular podem pressionar preços pela menor transferência de créditos.

### Possibilidade 2 — Regime Híbrido em 2027

- Carga projetada: **{money(totals['hibrido_2027'])}**.
- Alíquota efetiva projetada: **{projection.aliquota_hibrida_2027:.2%}**.
- Diferença contra o Por Dentro: **{money(delta_2027)}**.
- Crédito estimado das compras: **{money(totals['credito_compras_2027'])}**.
- Exige preparação documental, segregação do DAS residual e conciliação dos créditos.

### Possibilidade 3 — Cenário estrutural Híbrido 2033

- Carga projetada: **{money(totals['hibrido_2033'])}**.
- Alíquota efetiva projetada: **{projection.aliquota_hibrida_2033:.2%}**.
- Diferença contra o Por Dentro: **{money(delta_2033)}**.
- Crédito estimado das compras: **{money(totals['credito_compras_2033'])}**.
- Deve ser revisado quando forem confirmadas as alíquotas do destino e tratamentos diferenciados.

### Recomendação preliminar

Priorizar o estudo do **{preferred}**. A base conciliada indica que {projection.percentual_entradas_creditaveis:.2%} das compras projetadas pode gerar crédito, e a cobertura média de entradas corresponde a {input_coverage:.2%} das vendas. A recomendação é preliminar e deve ser validada por item, fornecedor, documento fiscal e fluxo financeiro.

### Fundamento da decisão

- A LC 123/2006 mantém a apuração do Simples por receita, Anexo, faixa e alíquota efetiva.
- A LC 214/2025 permite ao optante apurar CBS e IBS pelo regime regular; dentro do DAS, a própria empresa não apropria créditos desses tributos e o cliente do regime regular fica limitado ao montante devido pelo Simples.
- A escolha não deve comparar somente alíquotas: deve considerar perfil B2B/B2C, crédito das entradas, crédito ao cliente, preço, margem, capital de giro e custo de conformidade.
- A LC 214/2025 recebeu alterações posteriores, inclusive pela LC 227/2026; confirmar regulamentação e prazo de opção vigente.

### Plano de ação

1. Importar todos os meses disponíveis da Simulação da Reforma para reduzir dependência de uma única competência.
2. Conciliar a base de entradas com documentos efetivamente elegíveis a crédito.
3. Classificar clientes por CPF/CNPJ e regime para medir a dependência B2B real.
4. Simular repasse de preço, margem de contribuição e eventual perda de contratos.
5. Confirmar alíquotas, reduções, regimes específicos e cronograma antes da opção.
6. Preparar cadastro fiscal, documentos eletrônicos e controles de créditos.

### Validação do PGDAS

{pgdas_note}

> Análise gerencial no padrão consultivo do Grupo Nascel. Não substitui parecer tributário ou apuração oficial.
"""


def generate_report_with_gemini(
    local_report: str,
    projection: FutureProjection,
    api_key: str,
    model: str,
) -> str:
    """Enriquece o relatório pela API Gemini após ação explícita do usuário."""
    if not api_key or not model:
        raise AnalyticsError(
            "Configure GOOGLE_API_KEY (preferencial) ou GEMINI_API_KEY e selecione um modelo."
        )
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise AnalyticsError("A biblioteca google-genai não está instalada. Execute o arquivo iniciar_portal.bat.") from exc
    prompt = f"""Revise o relatório abaixo e produza uma versão executiva em português do Brasil no padrão consultivo do Grupo Nascel: clara, estratégica, humana e objetiva para um gestor que não domina tributação. Preserve rigorosamente os números, não invente regras ou alíquotas e não altere as conclusões sem explicar a razão.

Organize a resposta em:
1. resumo em linguagem simples;
2. o que foi considerado na análise;
3. explicação de cada cenário (Atual, Por Dentro, Híbrido 2027 e Híbrido 2033);
4. principais riscos e limitações;
5. recomendação condicional, deixando claro o que precisa ser validado;
6. plano de ação objetivo.

Diferencie fatos importados, cálculos do simulador e premissas editáveis. Explique siglas na primeira ocorrência. Compare impacto operacional e comercial, não apenas carga tributária. Inclua aviso de que o material não substitui parecer profissional e registre que a LC 214/2025 deve ser lida com suas alterações posteriores vigentes.

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
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=4_096,
            ),
        )
        if not response.text:
            raise AnalyticsError("O Gemini não retornou conteúdo textual.")
        return response.text
    except Exception as exc:
        if isinstance(exc, AnalyticsError):
            raise
        raise AnalyticsError(_friendly_gemini_error(exc, model)) from exc


def _friendly_gemini_error(exc: Exception, model: str = "") -> str:
    """Traduz erros comuns do SDK sem expor detalhes sensíveis da credencial."""
    text = str(exc).lower()
    if any(
        term in text
        for term in (
            "permission_denied", "unauthenticated", "api key not valid",
            "api_key_invalid", "401", "403",
        )
    ):
        return (
            "A chave do Gemini foi recusada. Verifique no Google AI Studio se ela está ativa, "
            "autorizada para a Gemini API e se não aparece como bloqueada. Chaves padrão "
            "irrestritas deixaram de ser aceitas em 19/06/2026; prefira uma nova chave de autorização."
        )
    if any(term in text for term in ("not_found", "not found", "404")):
        return (
            f"O modelo '{model}' não está disponível para esta chave ou versão da API. "
            "Use 'Testar conexão e atualizar modelos' para selecionar um modelo realmente liberado."
        )
    if any(term in text for term in ("resource_exhausted", "quota", "rate limit", "429")):
        return (
            "A cota ou o limite de uso do Gemini foi atingido. Aguarde e tente novamente, "
            "confira RPM/TPM/RPD e faturamento no Google AI Studio ou escolha um modelo Flash."
        )
    if any(term in text for term in ("failed_precondition", "free tier", "billing")):
        return (
            "O projeto não atende às condições da camada gratuita nesta região ou precisa de faturamento. "
            "Revise o projeto e o plano de cobrança no Google AI Studio."
        )
    if any(term in text for term in ("unavailable", "deadline_exceeded", "timeout", "503", "504", "500")):
        return (
            "O serviço do Gemini está temporariamente indisponível ou demorou além do limite. "
            "Tente novamente em alguns minutos ou selecione um modelo Flash."
        )
    if any(term in text for term in ("blocked", "safety", "finish_reason")):
        return (
            "A resposta foi bloqueada pelos filtros de segurança do Gemini. O relatório local continua disponível; "
            "revise o conteúdo enviado antes de tentar novamente."
        )
    return (
        f"Não foi possível concluir a análise pelo Gemini ({type(exc).__name__}). "
        "Teste a conexão, atualize a lista de modelos e confirme a chave e a cota no Google AI Studio."
    )


def list_gemini_models(api_key: str) -> list[str]:
    """Valida a chave e lista modelos de texto habilitados para generateContent."""
    if not api_key:
        raise AnalyticsError("Configure GOOGLE_API_KEY ou GEMINI_API_KEY antes de testar a conexão.")
    try:
        from google import genai
    except ImportError as exc:
        raise AnalyticsError("A biblioteca google-genai não está instalada. Execute o arquivo iniciar_portal.bat.") from exc
    try:
        client = genai.Client(api_key=api_key)
        excluded = ("image", "embedding", "tts", "audio", "live", "robotics", "computer-use")
        available = {
            str(model.name).removeprefix("models/")
            for model in client.models.list()
            if "generateContent" in (getattr(model, "supported_actions", None) or [])
            and str(model.name).removeprefix("models/").startswith("gemini-")
            and not any(term in str(model.name).lower() for term in excluded)
        }
        if not available:
            raise AnalyticsError(
                "A chave foi aceita, mas nenhum modelo de texto com generateContent está disponível neste projeto."
            )
        preference = {name: index for index, name in enumerate(GEMINI_MODEL_PREFERENCE)}
        return sorted(available, key=lambda name: (preference.get(name, len(preference)), name))
    except Exception as exc:
        if isinstance(exc, AnalyticsError):
            raise
        raise AnalyticsError(_friendly_gemini_error(exc)) from exc
