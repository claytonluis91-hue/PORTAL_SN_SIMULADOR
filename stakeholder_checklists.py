"""Checklists externos e consolidação interna para a decisão tributária de 2027."""

from __future__ import annotations

import io
import re
from typing import Any, Sequence

import pandas as pd

from analytics_engine import FutureProjection
from dominio_importers import DominioSimulationReport
from nascel_consulting import NASCEL_COLORS, NASCEL_NAME


OFFICIAL_REFERENCES = (
    (
        "Opção Simples e regime regular para 2027",
        "https://www8.receita.fazenda.gov.br/SimplesNacional/Noticias/NoticiaCompleta.aspx?id=c739e03c-8482-473f-8e82-f38ec3b13637",
        "Opção de setembro/2026 para janeiro–junho/2027 e nova oportunidade em março/2027.",
    ),
    (
        "LC 214/2025 compilada",
        "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp214compilado.htm",
        "Regime regular, não cumulatividade, documentos e créditos de IBS/CBS.",
    ),
)


CUSTOMER_QUESTIONS = (
    ("Cadastro", "Razão social, CNPJ e contato fiscal responsável.", "Texto e e-mail/telefone.", "ALTA", "Identificar e validar a resposta.", None),
    ("Regime 2027", "Qual será o regime tributário e a forma de apuração de IBS/CBS em 2027?", "Informe Simples por dentro, Simples com IBS/CBS regular, Lucro Presumido, Lucro Real ou outro.", "ALTA", "Separar clientes que poderão avaliar créditos.", ["Simples por dentro", "Simples com IBS/CBS regular", "Lucro Presumido", "Lucro Real", "Outro/indefinido"]),
    ("Crédito", "O crédito de IBS/CBS será relevante na escolha ou manutenção de fornecedores?", "Indique Sim, Não ou Em avaliação e explique o critério.", "ALTA", "Medir risco comercial do regime por dentro.", ["Sim", "Não", "Em avaliação"]),
    ("Comparação", "As propostas serão comparadas pelo preço bruto ou pelo custo líquido após créditos?", "Escolha a forma usada nas cotações e contratos.", "ALTA", "Quantificar eventual pressão de preço.", ["Preço bruto", "Custo líquido após créditos", "Ambos", "Ainda não definido"]),
    ("Preço", "Haverá pedido de desconto caso o crédito destacado/aproveitável seja inferior ao de fornecedores do regime regular?", "Informe Sim/Não e, se possível, percentual estimado.", "ALTA", "Estimar impacto comercial do Por Dentro.", ["Sim", "Não", "Em avaliação"]),
    ("Documento fiscal", "Quais campos e informações de IBS/CBS serão exigidos no documento fiscal?", "Liste CST, cClassTrib, NBS/NCM, alíquotas, valores e demais validações.", "ALTA", "Planejar emissão fiscal e evitar rejeições.", None),
    ("Classificação", "A operação possui NBS/NCM, cClassTrib ou tratamento diferenciado já validado?", "Informe códigos, redução, isenção, alíquota zero ou regime específico e anexe a fundamentação.", "ALTA", "Evitar comparar operações com tratamentos distintos.", None),
    ("Destino", "Qual é o município/UF de destino ou de fruição do serviço/bem?", "Informe local e regra contratual usada para determinar o destino.", "MÉDIA", "Validar local de incidência do IBS.", None),
    ("Contrato", "Os contratos serão revisados para preço, tributos, créditos, reajuste ou reequilíbrio em 2027?", "Indique cláusulas afetadas e prazo de revisão.", "ALTA", "Medir risco contratual e de margem.", ["Sim", "Não", "Em avaliação"]),
    ("Cadastro fornecedor", "Será necessário recadastrar ou homologar fornecedores em função do IBS/CBS?", "Informe requisitos, testes e prazo.", "MÉDIA", "Planejar continuidade comercial.", ["Sim", "Não", "Em avaliação"]),
    ("Sistemas", "O sistema de recebimento/escrituração estará preparado para validar IBS/CBS em janeiro de 2027?", "Informe ambiente de testes, leiautes e data prevista.", "MÉDIA", "Alinhar testes entre emissor e adquirente.", ["Sim", "Não", "Parcialmente", "Em avaliação"]),
    ("Pagamento", "Haverá mudança em prazo, retenção, glosa ou liberação de pagamento por divergência tributária?", "Descreva as novas regras e documentos necessários.", "MÉDIA", "Projetar capital de giro e risco de glosa.", None),
    ("Volume", "Qual a estimativa de compras da nossa empresa em 2027?", "Informe faixa ou valor estimado, se possível.", "MÉDIA", "Ponderar a resposta pelo faturamento esperado.", None),
    ("Evidências", "Quais documentos suportam as respostas fornecidas?", "Anexe política fiscal, comunicado, contrato, parecer ou manual de fornecedor.", "ALTA", "Dar rastreabilidade à decisão.", None),
)


SUPPLIER_QUESTIONS = (
    ("Cadastro", "Razão social, CNPJ e contato fiscal responsável.", "Texto e e-mail/telefone.", "ALTA", "Identificar e validar a resposta.", None),
    ("Regime 2027", "Qual será o regime tributário e a forma de apuração de IBS/CBS em 2027?", "Informe Simples por dentro, Simples com IBS/CBS regular, Lucro Presumido, Lucro Real ou outro.", "ALTA", "Determinar o potencial de crédito das compras.", ["Simples por dentro", "Simples com IBS/CBS regular", "Lucro Presumido", "Lucro Real", "Outro/indefinido"]),
    ("Documento fiscal", "Os documentos fiscais estarão preparados para IBS/CBS a partir de janeiro de 2027?", "Informe data de homologação e ambiente de testes.", "ALTA", "Reduzir risco de documento sem informação necessária.", ["Sim", "Não", "Parcialmente", "Em avaliação"]),
    ("Classificação", "Quais NCM/NBS, CST e cClassTrib serão usados nas operações conosco?", "Informe os códigos por produto/serviço e responsável pela validação.", "ALTA", "Classificar corretamente débito e crédito.", None),
    ("Tratamento", "Há redução, alíquota zero, isenção, diferimento ou regime específico aplicável?", "Informe percentuais de redução de IBS/CBS e fundamento legal.", "ALTA", "Calcular alíquota efetiva da compra.", None),
    ("Alíquotas", "Quais alíquotas estimadas de IBS e CBS serão destacadas em 2027?", "Informe IBS, CBS e total separadamente.", "ALTA", "Projetar o crédito potencial.", None),
    ("Crédito", "O documento e a operação permitirão crédito ao adquirente sujeito ao regime regular?", "Informe limitações, condições e momento esperado de apropriação.", "ALTA", "Separar compra contábil de compra creditável.", ["Sim", "Não", "Parcialmente", "Depende da operação"]),
    ("Pagamento", "Há condição relacionada ao pagamento/extinção do débito para liberação do crédito?", "Descreva evento, prazo e forma de comprovação.", "ALTA", "Projetar timing do crédito e capital de giro.", None),
    ("Destino", "Qual regra de local de incidência do IBS será aplicada?", "Informe município/UF, local da entrega/fruição e eventuais exceções.", "MÉDIA", "Validar IBS do destino.", None),
    ("Preço", "Os preços serão reajustados em razão da substituição de tributos e dos créditos?", "Informe metodologia, data-base e memória de cálculo.", "ALTA", "Evitar dupla incorporação de tributos ao preço.", ["Sim", "Não", "Em avaliação"]),
    ("Contrato", "Será necessária alteração contratual para tributos, créditos, reajustes ou reequilíbrio?", "Indique cláusulas e prazo para aditivo.", "MÉDIA", "Preparar contratos para 2027.", ["Sim", "Não", "Em avaliação"]),
    ("Devoluções", "Como serão tratados cancelamentos, devoluções, bonificações e ajustes?", "Descreva documentos, eventos e reflexo no crédito.", "MÉDIA", "Evitar manutenção indevida de créditos.", None),
    ("Fornecimento", "Qual o volume e a periodicidade estimados de fornecimento em 2027?", "Informe valor anual ou faixa e principais itens.", "MÉDIA", "Ponderar fornecedor na base creditável.", None),
    ("Evidências", "Quais documentos suportam a classificação e o tratamento informados?", "Anexe parecer, memória de cálculo, cadastro de itens e fundamento legal.", "ALTA", "Dar rastreabilidade ao crédito projetado.", None),
)


def _workbook_and_formats(output: io.BytesIO):
    import xlsxwriter

    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    workbook.set_properties({"author": NASCEL_NAME, "subject": "Preparação IBS/CBS 2027"})
    workbook.set_calc_mode("auto")
    formats = {
        "title": workbook.add_format({"bold": True, "font_size": 20, "font_color": "#FFFFFF", "bg_color": NASCEL_COLORS["navy"], "align": "center", "valign": "vcenter"}),
        "subtitle": workbook.add_format({"font_size": 10, "font_color": NASCEL_COLORS["slate"], "text_wrap": True, "valign": "top"}),
        "header": workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": NASCEL_COLORS["navy"], "border": 1, "border_color": NASCEL_COLORS["gold"], "align": "center", "valign": "vcenter", "text_wrap": True}),
        "text": workbook.add_format({"border": 1, "border_color": "#D7E0E8", "text_wrap": True, "valign": "top"}),
        "input": workbook.add_format({"bg_color": "#FFF4CC", "border": 1, "border_color": "#D7E0E8", "text_wrap": True, "valign": "top", "locked": False}),
        "money_input": workbook.add_format({"bg_color": "#FFF4CC", "border": 1, "num_format": 'R$ #,##0.00', "locked": False}),
        "percent_input": workbook.add_format({"bg_color": "#FFF4CC", "border": 1, "num_format": "0.00%", "locked": False}),
        "money": workbook.add_format({"border": 1, "num_format": 'R$ #,##0.00;[Red]-R$ #,##0.00'}),
        "percent": workbook.add_format({"border": 1, "num_format": "0.00%"}),
        "result": workbook.add_format({"bold": True, "border": 1, "bg_color": "#E8F3EE", "num_format": 'R$ #,##0.00;[Red]-R$ #,##0.00'}),
        "note": workbook.add_format({"italic": True, "font_color": NASCEL_COLORS["slate"], "text_wrap": True, "valign": "top"}),
        "link": workbook.add_format({"font_color": "blue", "underline": True, "text_wrap": True}),
    }
    return workbook, formats


def _write_questionnaire(
    title: str,
    audience: str,
    report: DominioSimulationReport,
    questions: Sequence[tuple[str, str, str, str, str, list[str] | None]],
) -> bytes:
    output = io.BytesIO()
    workbook, fmt = _workbook_and_formats(output)
    sheet = workbook.add_worksheet(f"Checklist_{audience}")
    sheet.hide_gridlines(2)
    sheet.freeze_panes(8, 0)
    sheet.set_landscape()
    sheet.fit_to_pages(1, 0)
    sheet.set_column("A:A", 6)
    sheet.set_column("B:B", 20)
    sheet.set_column("C:C", 48)
    sheet.set_column("D:D", 38)
    sheet.set_column("E:E", 28)
    sheet.set_column("F:F", 36)
    sheet.set_column("G:G", 12)
    sheet.set_column("H:H", 38)
    sheet.set_row(0, 34)
    sheet.merge_range("A1:H2", title, fmt["title"])
    sheet.merge_range(
        "A3:H3",
        "Objetivo: coletar informações para a preparação de 2027. Este documento não comunica uma opção tributária nem substitui validação fiscal.",
        fmt["subtitle"],
    )
    sheet.write("A5", "Empresa remetente", fmt["header"])
    sheet.merge_range("B5:D5", report.empresa, fmt["text"])
    sheet.write("E5", "CNPJ", fmt["header"])
    sheet.merge_range("F5:H5", report.cnpj, fmt["text"])
    sheet.write("A6", "Destinatário", fmt["header"])
    sheet.merge_range("B6:D6", "Preencher", fmt["input"])
    sheet.write("E6", "Prazo de resposta", fmt["header"])
    sheet.merge_range("F6:H6", "Preencher", fmt["input"])
    headers = ["Nº", "Tema", "Pergunta", "Como responder", "Resposta do destinatário", "Evidência / observação", "Prioridade", "Uso na análise"]
    sheet.write_row(7, 0, headers, fmt["header"])
    for index, (theme, question, guidance, priority, purpose, choices) in enumerate(questions, start=1):
        row = 7 + index
        sheet.set_row(row, 62)
        values = [index, theme, question, guidance]
        for column, value in enumerate(values):
            sheet.write(row, column, value, fmt["text"])
        sheet.write_blank(row, 4, None, fmt["input"])
        sheet.write_blank(row, 5, None, fmt["input"])
        sheet.write(row, 6, priority, fmt["text"])
        sheet.write(row, 7, purpose, fmt["text"])
        if choices:
            sheet.data_validation(row, 4, row, 4, {"validate": "list", "source": choices})
    footer_row = 9 + len(questions)
    sheet.merge_range(
        footer_row,
        0,
        footer_row + 1,
        7,
        "Confirmação do destinatário: as respostas refletem o entendimento disponível nesta data e deverão ser atualizadas caso o regime, a classificação ou os procedimentos fiscais sejam alterados.",
        fmt["note"],
    )
    sheet.protect("", {"select_unlocked_cells": True, "select_locked_cells": True})
    workbook.close()
    return output.getvalue()


def build_customer_checklist(report: DominioSimulationReport) -> bytes:
    return _write_questionnaire(
        "Checklist 2027 para Clientes · IBS e CBS",
        "Clientes",
        report,
        CUSTOMER_QUESTIONS,
    )


def build_supplier_checklist(report: DominioSimulationReport) -> bytes:
    return _write_questionnaire(
        "Checklist 2027 para Fornecedores · IBS e CBS",
        "Fornecedores",
        report,
        SUPPLIER_QUESTIONS,
    )


def _weighted_rate(
    reports: Sequence[DominioSimulationReport], attribute: str, *, input_rate: bool = False
) -> float:
    denominator = sum(
        item.base_entradas_credito if input_rate else item.base_saidas for item in reports
    )
    if denominator <= 0:
        return 0.0
    return sum(
        (item.base_entradas_credito if input_rate else item.base_saidas)
        * getattr(item, attribute)
        for item in reports
    ) / denominator


def build_2027_decision_workbook(
    report: DominioSimulationReport,
    reports: Sequence[DominioSimulationReport],
    projection: FutureProjection,
) -> bytes:
    """Planilha interna para consolidar respostas e recalcular o cenário de 2027."""
    reports = list(reports)
    totals = projection.totais
    revenue = totals["receita"]
    purchases = totals["entradas"]
    creditable_share = projection.percentual_entradas_creditaveis
    regular_customer_share = projection.percentual_operacoes_creditaveis
    inside_rate = projection.aliquota_por_dentro
    total_base = sum(item.base_saidas for item in reports) or 1.0
    residual_rate = sum(item.fase_2027["simples_residual"] for item in reports) / total_base
    cbs_debit_rate = _weighted_rate(reports, "aliquota_cbs_2027")
    ibs_debit_rate = _weighted_rate(reports, "aliquota_ibs_2027")
    cbs_credit_rate = _weighted_rate(reports, "aliquota_credito_cbs_2027", input_rate=True)
    ibs_credit_rate = _weighted_rate(reports, "aliquota_credito_ibs_2027", input_rate=True)
    creditable_base = purchases * creditable_share
    cbs_credit = creditable_base * cbs_credit_rate
    ibs_credit = creditable_base * ibs_credit_rate
    residual = revenue * residual_rate
    cbs_net = max(revenue * cbs_debit_rate - cbs_credit, 0.0)
    ibs_net = max(revenue * ibs_debit_rate - ibs_credit, 0.0)
    hybrid = residual + cbs_net + ibs_net
    inside = revenue * inside_rate

    output = io.BytesIO()
    workbook, fmt = _workbook_and_formats(output)

    guide = workbook.add_worksheet("Como_Usar")
    guide.hide_gridlines(2)
    guide.set_column("A:A", 28)
    guide.set_column("B:B", 100)
    guide.merge_range("A1:B2", "Consolidação interna · Decisão IBS/CBS 2027", fmt["title"])
    guide.write_row(3, 0, ["Etapa", "Orientação"], fmt["header"])
    guide_rows = [
        ("1. Enviar", "Encaminhe apenas o checklist correspondente a cada cliente ou fornecedor; não envie esta consolidação interna."),
        ("2. Registrar", "Transcreva as respostas nas abas Respostas_Clientes e Respostas_Fornecedores, ponderando os participantes por receita ou compras."),
        ("3. Consolidar", "Atualize as células amarelas da Calculadora_2027 com os percentuais apurados e os custos internos estimados."),
        ("4. Comparar", "Analise a diferença tributária e o impacto econômico comparável; valores negativos favorecem o Híbrido nas premissas informadas."),
        ("5. Validar", "Confirme documentos, NBS/NCM, cClassTrib, tratamentos diferenciados, contratos, preço, margem e capital de giro."),
        ("Prazo", "A opção para janeiro–junho/2027 ocorre em setembro/2026; a Receita informou nova oportunidade em março/2027 para o segundo semestre."),
    ]
    for row_index, row in enumerate(guide_rows, start=4):
        guide.write(row_index, 0, row[0], fmt["text"])
        guide.write(row_index, 1, row[1], fmt["text"])
        guide.set_row(row_index, 45)

    customer = workbook.add_worksheet("Respostas_Clientes")
    customer.freeze_panes(5, 0)
    customer.set_column("A:B", 20)
    customer.set_column("C:C", 14)
    customer.set_column("D:F", 24)
    customer.set_column("G:G", 18)
    customer.set_column("H:J", 24)
    customer.merge_range("A1:J2", "Consolidação das respostas de clientes", fmt["title"])
    customer.merge_range("A3:J3", "Preencha uma linha por cliente. Percentuais devem totalizar aproximadamente 100% da receita analisada.", fmt["subtitle"])
    customer_headers = ["CNPJ", "Razão social", "% da receita", "Regime IBS/CBS 2027", "Crédito é relevante?", "Compara custo líquido?", "Pressão de preço estimada", "Destino / incidência", "Tratamento diferenciado", "Validado / observações"]
    customer.add_table(4, 0, 104, len(customer_headers) - 1, {"name": "RespostasClientes", "style": "Table Style Medium 2", "columns": [{"header": value} for value in customer_headers]})
    customer.data_validation(5, 3, 104, 3, {"validate": "list", "source": ["Simples por dentro", "Simples com IBS/CBS regular", "Lucro Presumido", "Lucro Real", "Outro/indefinido"]})
    customer.data_validation(5, 4, 104, 5, {"validate": "list", "source": ["Sim", "Não", "Em avaliação"]})
    customer.set_column(2, 2, 14, fmt["percent_input"])
    customer.set_column(6, 6, 18, fmt["percent_input"])

    supplier = workbook.add_worksheet("Respostas_Fornecedores")
    supplier.freeze_panes(5, 0)
    supplier.set_column("A:B", 20)
    supplier.set_column("C:C", 14)
    supplier.set_column("D:F", 24)
    supplier.set_column("G:K", 16)
    supplier.set_column("L:M", 28)
    supplier.merge_range("A1:M2", "Consolidação das respostas de fornecedores", fmt["title"])
    supplier.merge_range("A3:M3", "Preencha uma linha por fornecedor. Percentuais devem totalizar aproximadamente 100% das compras analisadas.", fmt["subtitle"])
    supplier_headers = ["CNPJ", "Razão social", "% das compras", "Regime IBS/CBS 2027", "DFe preparado?", "NCM/NBS/cClassTrib", "Redução IBS", "Redução CBS", "Alíquota IBS", "Alíquota CBS", "% creditável", "Prazo / liberação do crédito", "Validado / observações"]
    supplier.add_table(4, 0, 104, len(supplier_headers) - 1, {"name": "RespostasFornecedores", "style": "Table Style Medium 4", "columns": [{"header": value} for value in supplier_headers]})
    supplier.data_validation(5, 3, 104, 3, {"validate": "list", "source": ["Simples por dentro", "Simples com IBS/CBS regular", "Lucro Presumido", "Lucro Real", "Outro/indefinido"]})
    supplier.data_validation(5, 4, 104, 4, {"validate": "list", "source": ["Sim", "Não", "Parcialmente", "Em avaliação"]})
    for column in (2, 6, 7, 8, 9, 10):
        supplier.set_column(column, column, 16, fmt["percent_input"])

    calculator = workbook.add_worksheet("Calculadora_2027")
    calculator.hide_gridlines(2)
    calculator.set_column("A:A", 42)
    calculator.set_column("B:B", 22)
    calculator.set_column("C:C", 72)
    calculator.set_column("D:D", 38)
    calculator.merge_range("A1:D2", "Calculadora interna · Por Dentro × Híbrido 2027", fmt["title"])
    calculator.merge_range("A3:D3", f"{report.empresa} · CNPJ {report.cnpj}. Células amarelas são premissas editáveis; substitua-as pela consolidação das respostas.", fmt["subtitle"])
    calculator.write_row(3, 0, ["Premissa", "Valor", "Origem / como ajustar", "Uso"], fmt["header"])
    assumptions = [
        ("Receita anual projetada", revenue, fmt["money_input"], "Projeção do portal", "Base dos débitos"),
        ("Compras anuais projetadas", purchases, fmt["money_input"], "Projeção do portal", "Base potencial de créditos"),
        ("% das compras creditáveis", creditable_share, fmt["percent_input"], "Atualizar após fornecedores", "Define a base creditável"),
        ("% da receita em clientes do regime regular", regular_customer_share, fmt["percent_input"], "Atualizar após clientes", "Pondera risco comercial"),
        ("Pressão/desconto comercial se Por Dentro", 0.0, fmt["percent_input"], "Estimar pelas respostas dos clientes", "Impacto comercial estimado"),
        ("Alíquota efetiva Por Dentro", inside_rate, fmt["percent_input"], "Projeção do portal", "Carga no DAS"),
        ("Alíquota do DAS residual no Híbrido", residual_rate, fmt["percent_input"], "Simulação Domínio", "Parte residual do DAS"),
        ("Alíquota de débito CBS 2027", cbs_debit_rate, fmt["percent_input"], "Simulação Domínio", "Débito CBS"),
        ("Alíquota de débito IBS 2027", ibs_debit_rate, fmt["percent_input"], "Simulação Domínio", "Débito IBS"),
        ("Alíquota de crédito CBS", cbs_credit_rate, fmt["percent_input"], "Validar com fornecedores", "Crédito CBS"),
        ("Alíquota de crédito IBS", ibs_credit_rate, fmt["percent_input"], "Validar com fornecedores", "Crédito IBS"),
        ("Custo operacional adicional mensal", 0.0, fmt["money_input"], "Estimar sistemas, pessoas e compliance", "Custo anual do Híbrido"),
    ]
    for row_index, (label, value, value_format, source, purpose) in enumerate(assumptions, start=4):
        calculator.write(row_index, 0, label, fmt["text"])
        calculator.write(row_index, 1, value, value_format)
        calculator.write(row_index, 2, source, fmt["text"])
        calculator.write(row_index, 3, purpose, fmt["text"])
    calculator.write_row(17, 0, ["Resultado", "Valor", "Fórmula", "Leitura"], fmt["header"])
    result_rows = [
        (18, "Carga tributária Por Dentro", "=B5*B10", inside, "Tributos estimados dentro do DAS"),
        (19, "Base creditável das compras", "=B6*B7", creditable_base, "Compras × percentual creditável"),
        (20, "Crédito CBS", "=B20*B14", cbs_credit, "Crédito sujeito à validação"),
        (21, "Crédito IBS", "=B20*B15", ibs_credit, "Crédito sujeito à validação"),
        (22, "DAS residual no Híbrido", "=B5*B11", residual, "Demais tributos no Simples"),
        (23, "CBS líquida", "=MAX(B5*B12-B21,0)", cbs_net, "Débito menos crédito CBS"),
        (24, "IBS líquido", "=MAX(B5*B13-B22,0)", ibs_net, "Débito menos crédito IBS"),
        (25, "Carga tributária Híbrida", "=SUM(B23:B25)", hybrid, "DAS residual + CBS + IBS"),
        (26, "Diferença tributária Híbrido - Por Dentro", "=B26-B19", hybrid - inside, "Negativo favorece o Híbrido"),
        (27, "Custo operacional adicional anual", "=B16*12", 0.0, "Sistemas, equipe e compliance"),
        (28, "Risco comercial estimado do Por Dentro", "=B5*B8*B9", 0.0, "Receita regular × pressão de preço"),
        (29, "Impacto econômico comparável Por Dentro", "=B19+B29", inside, "Tributos + risco comercial"),
        (30, "Impacto econômico comparável Híbrido", "=B26+B28", hybrid, "Tributos + custo operacional"),
        (31, "Diferença ajustada Híbrido - Por Dentro", "=B31-B30", hybrid - inside, "Negativo favorece o Híbrido"),
    ]
    for row_index, label, formula, cached_value, reading in result_rows:
        calculator.write(row_index, 0, label, fmt["text"])
        calculator.write_formula(row_index, 1, formula, fmt["result"], cached_value)
        calculator.write(row_index, 2, formula[1:], fmt["text"])
        calculator.write(row_index, 3, reading, fmt["text"])
    indication = "HÍBRIDO requer aprofundamento" if hybrid - inside < 0 else "POR DENTRO requer aprofundamento"
    calculator.write(32, 0, "Indicação matemática preliminar", fmt["header"])
    calculator.write_formula(32, 1, '=IF(B32<0,"HÍBRIDO requer aprofundamento","POR DENTRO requer aprofundamento")', fmt["header"], indication)
    calculator.merge_range("A35:D36", "A indicação matemática não formaliza opção. Valide enquadramento, classificação, documentos, contratos, margem, capital de giro, ressarcimentos e custos de conformidade.", fmt["note"])

    sources = workbook.add_worksheet("Fontes_Oficiais")
    sources.set_column("A:A", 40)
    sources.set_column("B:B", 105)
    sources.set_column("C:C", 75)
    sources.write_row(0, 0, ["Documento", "URL", "Aplicação"], fmt["header"])
    for row_index, (document, url, scope) in enumerate(OFFICIAL_REFERENCES, start=1):
        sources.write(row_index, 0, document, fmt["text"])
        sources.write_url(row_index, 1, url, fmt["link"], url)
        sources.write(row_index, 2, scope, fmt["text"])

    workbook.close()
    return output.getvalue()


def build_stakeholder_files(
    report: DominioSimulationReport,
    reports: Sequence[DominioSimulationReport],
    projection: FutureProjection,
) -> dict[str, bytes]:
    safe_company = re.sub(r"[^A-Za-z0-9]+", "_", report.empresa).strip("_")[:45]
    return {
        f"Checklist_Clientes_2027_{safe_company}.xlsx": build_customer_checklist(report),
        f"Checklist_Fornecedores_2027_{safe_company}.xlsx": build_supplier_checklist(report),
        f"Consolidacao_Decisao_2027_{safe_company}.xlsx": build_2027_decision_workbook(report, reports, projection),
    }
