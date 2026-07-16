import unittest
import zipfile
from io import BytesIO

import pandas as pd

from analytics_engine import build_future_projection
from dominio_importers import DominioSimulationReport, MonthlyReport
from stakeholder_checklists import build_stakeholder_files


class StakeholderChecklistTests(unittest.TestCase):
    @staticmethod
    def fixtures():
        empty = pd.DataFrame(columns=["Descrição", "Valor", "Percentual"])
        report = DominioSimulationReport(
            empresa="EMPRESA TESTE SERVIÇOS LTDA",
            cnpj="15435515000191",
            periodo=pd.Timestamp("2026-03-01"),
            tributos_atuais={"Total": 8_000.0, "IRPJ": 0.0, "CSLL": 0.0, "INSS/CPP": 0.0, "IPI": 0.0, "ICMS": 0.0, "ISS": 0.0, "PIS/Pasep": 0.0, "COFINS": 0.0},
            fase_2027={"simples_residual": 2_000.0, "cbs": 5_000.0, "ibs": 2_000.0, "total": 9_000.0, "diferenca": 1_000.0, "diferenca_percentual": 0.125},
            fase_2033={"simples_residual": 1_000.0, "cbs": 6_000.0, "ibs": 3_000.0, "total": 10_000.0, "diferenca": 2_000.0, "diferenca_percentual": 0.25},
            base_saidas=100_000.0,
            base_entradas_credito=60_000.0,
            aliquota_cbs_2027=0.07,
            aliquota_ibs_2027=0.02,
            aliquota_cbs_2033=0.18,
            aliquota_ibs_2033=0.08,
            aliquota_credito_cbs_2027=0.07,
            aliquota_credito_ibs_2027=0.02,
            aliquota_credito_cbs_2033=0.18,
            aliquota_credito_ibs_2033=0.08,
            saidas_por_acumulador=empty,
            entradas_por_acumulador=empty,
            clientes_por_regime=empty,
            fornecedores_por_regime=empty,
            vendas_nao_contribuinte=20_000.0,
            percentual_operacoes_creditaveis=0.8,
        )
        monthly = MonthlyReport(
            empresa=report.empresa,
            cnpj=report.cnpj,
            periodo_inicial=pd.Timestamp("2026-01-01"),
            periodo_final=pd.Timestamp("2026-03-31"),
            movimentos=pd.DataFrame(
                {
                    "Competência": pd.date_range("2026-01-01", periods=3, freq="MS"),
                    "Entradas": [60_000.0, 60_000.0, 60_000.0],
                    "Saídas": [100_000.0, 100_000.0, 100_000.0],
                    "Serviços": [0.0, 0.0, 0.0],
                }
            ),
        )
        return report, build_future_projection([report], monthly, average_months=3)

    def test_builds_separate_external_and_internal_workbooks(self) -> None:
        report, projection = self.fixtures()
        files = build_stakeholder_files(report, [report], projection)

        self.assertEqual(len(files), 3)
        self.assertTrue(all(content.startswith(b"PK") for content in files.values()))
        customer_name = next(name for name in files if name.startswith("Checklist_Clientes"))
        supplier_name = next(name for name in files if name.startswith("Checklist_Fornecedores"))
        decision_name = next(name for name in files if name.startswith("Consolidacao_Decisao"))

        customer_book = pd.ExcelFile(BytesIO(files[customer_name]), engine="calamine")
        supplier_book = pd.ExcelFile(BytesIO(files[supplier_name]), engine="calamine")
        decision_book = pd.ExcelFile(BytesIO(files[decision_name]), engine="calamine")
        self.assertEqual(customer_book.sheet_names, ["Checklist_Clientes"])
        self.assertEqual(supplier_book.sheet_names, ["Checklist_Fornecedores"])
        self.assertEqual(
            decision_book.sheet_names,
            ["Como_Usar", "Respostas_Clientes", "Respostas_Fornecedores", "Calculadora_2027", "Fontes_Oficiais"],
        )

    def test_questionnaires_and_calculator_contain_expected_controls(self) -> None:
        report, projection = self.fixtures()
        files = build_stakeholder_files(report, [report], projection)
        customer = next(content for name, content in files.items() if name.startswith("Checklist_Clientes"))
        decision = next(content for name, content in files.items() if name.startswith("Consolidacao_Decisao"))

        questions = pd.read_excel(BytesIO(customer), sheet_name="Checklist_Clientes", header=7, engine="calamine")
        self.assertGreaterEqual(len(questions.dropna(subset=["Pergunta"])), 12)
        self.assertIn("Resposta do destinatário", questions.columns)
        self.assertIn("Uso na análise", questions.columns)

        calculator = pd.read_excel(BytesIO(decision), sheet_name="Calculadora_2027", header=None, engine="calamine")
        self.assertEqual(calculator.iat[18, 0], "Carga tributária Por Dentro")
        self.assertAlmostEqual(float(calculator.iat[18, 1]), projection.totais["por_dentro"])
        self.assertEqual(calculator.iat[32, 0], "Indicação matemática preliminar")

        with zipfile.ZipFile(BytesIO(decision)) as archive:
            formulas = "".join(
                archive.read(name).decode("utf-8")
                for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
        self.assertIn("B5*B10", formulas)
        self.assertIn("IF(B32&lt;0", formulas)


if __name__ == "__main__":
    unittest.main()
