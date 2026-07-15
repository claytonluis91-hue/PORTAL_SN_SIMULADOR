"""Padrão consultivo e visual do Grupo Nascel para os relatórios tributários."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from dominio_importers import (
    DominioSimulationReport,
    MonthlyReport,
    PGDASReport,
    cnpjs_are_compatible,
)
from simples_lc214 import SimplesLC214Simulation


NASCEL_COLORS = {
    "navy": "#16163F",
    "gold": "#F9AF44",
    "cream": "#F8F3EC",
    "white": "#FFFFFF",
    "ink": "#20202D",
    "slate": "#5B5B6E",
    "green": "#2E7D5B",
    "red": "#B5473C",
    "light_gold": "#FFF3DC",
    "light_navy": "#ECECF5",
}

NASCEL_NAME = "Grupo Nascel"
NASCEL_UNIT = "Nascel Contabilidade"
NASCEL_TAGLINE = "Estratégia tributária com clareza, segurança e visão de negócio."
NASCEL_LOGO_URL = "https://nascel.com.br/wp-content/uploads/2024/01/logo-nascel-branca.png"


@dataclass(frozen=True)
class NascelDiagnostic:
    score: int
    status: str
    recommendation: str
    rationale: str
    checklist: pd.DataFrame


def legal_timeline_frame() -> pd.DataFrame:
    """Cronograma executivo da transição relevante para a tomada de decisão."""
    return pd.DataFrame(
        [
            {
                "Período": "2026",
                "O que muda": "Ano de teste de CBS e IBS e adaptação dos documentos fiscais eletrônicos.",
                "Impacto no Simples": "A alíquota de teste não integra esta simulação do DAS; o foco é cadastro, documento e conformidade.",
                "Ação recomendada": "Revisar ERP, NCM/NBS, cadastro de clientes e fornecedores e leiautes de NF-e/NFS-e.",
                "Base legal": "LC 214/2025 e orientações RTC 2026 da RFB.",
            },
            {
                "Período": "2027–2028",
                "O que muda": "Novas tabelas e partilhas do Simples; escolha entre CBS/IBS dentro do DAS ou no regime regular.",
                "Impacto no Simples": "Dentro: menor complexidade e crédito limitado ao valor pago no Simples. Fora: apuração regular e crédito integral conforme as regras gerais.",
                "Ação recomendada": "Para jan–jun/2027, decidir entre 1º e 30/09/2026. Se não optar pelo regime regular, CBS/IBS ficam no DAS; haverá nova oportunidade em março/2027 para jul–dez/2027.",
                "Base legal": "LC 123/2006, arts. 18 e 23; LC 214/2025, arts. 41, §3º, e 47, §9º; Resolução CGSN 186/2026.",
            },
            {
                "Período": "2029–2032",
                "O que muda": "Redução progressiva de ICMS/ISS e aumento gradual do IBS.",
                "Impacto no Simples": "A diferença econômica entre os cenários deve ser recalculada a cada etapa da transição.",
                "Ação recomendada": "Atualizar alíquotas, contratos, preços e matriz de fornecedores a cada exercício.",
                "Base legal": "EC 132/2023 e LC 214/2025, conforme regulamentação vigente.",
            },
            {
                "Período": "2033",
                "O que muda": "Vigência integral do novo modelo e extinção de ICMS e ISS.",
                "Impacto no Simples": "O cenário estrutural passa a depender integralmente da cadeia de créditos, destino e tratamentos aplicáveis.",
                "Ação recomendada": "Revalidar enquadramento, rentabilidade por cliente/produto e estratégia comercial.",
                "Base legal": "EC 132/2023 e LC 214/2025.",
            },
        ]
    )


def official_sources_frame() -> pd.DataFrame:
    """Fontes primárias exibidas no relatório para permitir revisão da base legal."""
    return pd.DataFrame(
        [
            {
                "Documento": "LC 123/2006 — texto atualizado",
                "Escopo": "Simples Nacional, alíquota efetiva, Anexos, partilha e crédito ao adquirente.",
                "URL": "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp123.htm",
            },
            {
                "Documento": "LC 214/2025 — texto compilado",
                "Escopo": "IBS/CBS, regime regular, não cumulatividade e opção do Simples.",
                "URL": "https://www.presidencia.gov.br/ccivil_03/leis/lcp/lcp214compilado.htm",
            },
            {
                "Documento": "Anexo XIX da LC 214/2025 — tabelas 2027/2028",
                "Escopo": "Anexos do Simples, alíquotas e partilhas oficiais; o Anexo II inclui IPI.",
                "URL": "https://legis.senado.leg.br/norma/40180341/publicacao/40181111",
            },
            {
                "Documento": "EC 132/2023 — texto oficial",
                "Escopo": "Regra constitucional de transição e redução geral das alíquotas do IPI.",
                "URL": "https://www.planalto.gov.br/ccivil_03/constituicao/emendas/emc/emc132.htm",
            },
            {
                "Documento": "Cronograma oficial da Reforma do Consumo",
                "Escopo": "Transição de 2026 a 2033 e substituição gradual dos tributos.",
                "URL": "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/acoes-e-programas/programas-e-atividades/reforma-tributaria-do-consumo/entenda",
            },
            {
                "Documento": "Prazos do Simples e IBS/CBS para 2027",
                "Escopo": "Opção de setembro/2026, efeitos no primeiro semestre e nova oportunidade em março/2027.",
                "URL": "https://www8.receita.fazenda.gov.br/SIMPLESNACIONAL/noticias/noticiacompleta.aspx?id=C739E03C-8482-473F-8E82-F38EC3B13637",
            },
        ]
    )


def decision_matrix_frame(
    report: DominioSimulationReport,
    simulation: SimplesLC214Simulation,
) -> pd.DataFrame:
    """Compara os dois caminhos legais do Simples em linguagem gerencial."""
    purchase_credit = report.base_entradas_credito * (
        report.aliquota_credito_cbs_2027 + report.aliquota_credito_ibs_2027
    )
    return pd.DataFrame(
        [
            {
                "Caminho": "CBS/IBS dentro do DAS",
                "Como funciona": "CBS e IBS permanecem na guia unificada do Simples Nacional.",
                "Crédito estimado das compras": 0.0,
                "Desembolso estimado": report.tributos_atuais["Total"],
                "Tende a favorecer": "Empresas que priorizam simplicidade operacional ou possuem baixa base de compras creditáveis.",
                "Principal cuidado": "A empresa não aproveita créditos de IBS/CBS das entradas.",
                "Base legal": "LC 214/2025, art. 47, §9º; LC 123/2006, art. 23.",
            },
            {
                "Caminho": "CBS/IBS fora do DAS",
                "Como funciona": "O DAS fica residual e CBS/IBS são apurados pelo regime regular.",
                "Crédito estimado das compras": purchase_credit,
                "Desembolso estimado": report.fase_2027["total"],
                "Tende a favorecer": "Empresas com compras creditáveis relevantes e cadeia documental bem controlada.",
                "Principal cuidado": "Exige maior governança fiscal, conciliação, capital de giro e revisão de preço/margem.",
                "Base legal": "LC 214/2025, art. 41, §3º, e regras gerais de não cumulatividade.",
            },
        ]
    )


def _period_inputs(report: DominioSimulationReport, monthly: MonthlyReport) -> float:
    period_rows = monthly.movimentos[
        monthly.movimentos["Competência"].dt.to_period("M")
        == report.periodo.to_period("M")
    ]
    return float(period_rows["Entradas"].sum()) if not period_rows.empty else 0.0


def build_nascel_diagnostic(
    report: DominioSimulationReport,
    monthly: MonthlyReport,
    pgdas: PGDASReport | None,
    reports: Sequence[DominioSimulationReport],
    simulation: SimplesLC214Simulation,
) -> NascelDiagnostic:
    """Mede a robustez das informações antes de sugerir uma decisão tributária."""
    rows: list[dict[str, object]] = []

    def add(item: str, points: int, maximum: int, evidence: str, action: str) -> None:
        rows.append(
            {
                "Validação": item,
                "Pontuação": points,
                "Máximo": maximum,
                "Situação": "OK" if points == maximum else "REVISAR" if points else "PENDENTE",
                "Evidência": evidence,
                "Próxima ação": action,
            }
        )

    periods = len({item.periodo.to_period("M") for item in reports})
    history_points = 25 if periods >= 6 else 15 if periods >= 3 else 5
    add(
        "Histórico das simulações",
        history_points,
        25,
        f"{periods} competência(s) importada(s).",
        "Usar ao menos 6 competências para reduzir o efeito de sazonalidade."
        if periods < 6 else "Manter atualização mensal.",
    )

    pgdas_ok = bool(pgdas and cnpjs_are_compatible(report.cnpj, pgdas))
    add(
        "PGDAS e enquadramento",
        20 if pgdas_ok else 0,
        20,
        f"PGDAS compatível; Anexo {pgdas.anexo} e RBT12 conferidos."
        if pgdas_ok else "PGDAS ausente ou incompatível com o CNPJ analisado.",
        "Importar e conferir o PGDAS da competência."
        if not pgdas_ok else "Revalidar Anexo, atividade e RBT12 antes de cada opção.",
    )

    period_inputs = _period_inputs(report, monthly)
    difference = period_inputs - report.base_entradas_credito
    tolerance = abs(difference) / max(abs(period_inputs), abs(report.base_entradas_credito), 1.0)
    reconciliation_points = 20 if tolerance <= 0.01 else 10 if tolerance <= 0.05 else 0
    add(
        "Conciliação das entradas",
        reconciliation_points,
        20,
        f"Diferença de R$ {difference:,.2f} ({tolerance:.2%}) entre demonstrativo e base de crédito.",
        "Conciliar por documento e excluir aquisições sem direito a crédito."
        if reconciliation_points < 20 else "Conciliação dentro da tolerância gerencial de 1%.",
    )

    customers_ok = not report.clientes_por_regime.empty
    add(
        "Perfil dos clientes",
        15 if customers_ok else 0,
        15,
        f"{len(report.clientes_por_regime)} classificação(ões) por regime disponíveis."
        if customers_ok else "Não há classificação de clientes por regime.",
        "Validar CNPJ/CPF, regime e participação no faturamento por cliente.",
    )

    suppliers_ok = not report.fornecedores_por_regime.empty
    add(
        "Perfil dos fornecedores",
        10 if suppliers_ok else 0,
        10,
        f"{len(report.fornecedores_por_regime)} classificação(ões) por regime disponíveis."
        if suppliers_ok else "Não há classificação de fornecedores por regime.",
        "Validar fornecedores, documentos e créditos efetivamente aproveitáveis.",
    )

    monthly_periods = len(monthly.movimentos)
    monthly_points = 10 if monthly_periods >= 12 else 5 if monthly_periods >= 3 else 0
    add(
        "Histórico mensal",
        monthly_points,
        10,
        f"{monthly_periods} mês(es) no Demonstrativo Mensal.",
        "Usar 12 meses ou mais para tendência e projeção."
        if monthly_periods < 12 else "Histórico suficiente para leitura anual.",
    )

    score = int(sum(int(row["Pontuação"]) for row in rows))
    status = (
        "PRONTO PARA DECISÃO ASSISTIDA"
        if score >= 80
        else "REVISAR PREMISSAS"
        if score >= 60
        else "DADOS INSUFICIENTES"
    )
    delta = report.fase_2027["total"] - report.tributos_atuais["Total"]
    if score < 60:
        recommendation = "Não formalizar a opção antes de completar as validações pendentes."
    elif report.percentual_operacoes_creditaveis >= 0.60:
        recommendation = "Priorizar a validação do CBS/IBS fora do DAS, sem descartar o cenário por dentro."
    else:
        recommendation = "Priorizar a validação do CBS/IBS dentro do DAS, mantendo teste comercial do cenário por fora."
    direction = "aumenta" if delta >= 0 else "reduz"
    rationale = (
        f"{report.percentual_operacoes_creditaveis:.2%} das saídas são potencialmente sensíveis a crédito. "
        f"Na competência analisada, o caminho fora do DAS {direction} o desembolso estimado em "
        f"R$ {abs(delta):,.2f}, conforme a simulação do Domínio e a base de créditos informada; "
        "preço, margem e capital de giro ainda precisam ser avaliados."
    )
    return NascelDiagnostic(
        score=score,
        status=status,
        recommendation=recommendation,
        rationale=rationale,
        checklist=pd.DataFrame(rows),
    )
