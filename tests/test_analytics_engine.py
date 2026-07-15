import unittest

import pandas as pd

from analytics_engine import build_future_projection
from dashboard_exports import scenario_table
from dominio_importers import DominioSimulationReport, MonthlyReport
from simples_lc214 import simulate_lc214_2027_2028


class FutureProjectionCreditTests(unittest.TestCase):
    def setUp(self) -> None:
        empty = pd.DataFrame()
        self.report = DominioSimulationReport(
            empresa="Empresa Teste",
            cnpj="12345678000190",
            periodo=pd.Timestamp("2026-01-01"),
            tributos_atuais={
                "Total": 8_000.0,
                "ICMS": 2_000.0,
                "ISS": 0.0,
                "PIS/Pasep": 500.0,
                "COFINS": 1_500.0,
            },
            fase_2027={
                "simples_residual": 2_000.0,
                "cbs": 5_000.0,
                "ibs": 2_000.0,
                "total": 9_000.0,
                "diferenca": 1_000.0,
                "diferenca_percentual": 0.125,
            },
            fase_2033={
                "simples_residual": 1_000.0,
                "cbs": 6_000.0,
                "ibs": 3_000.0,
                "total": 10_000.0,
                "diferenca": 2_000.0,
                "diferenca_percentual": 0.25,
            },
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
            vendas_nao_contribuinte=25_000.0,
            percentual_operacoes_creditaveis=0.75,
        )
        self.monthly = MonthlyReport(
            empresa="Empresa Teste",
            cnpj="12345678000190",
            periodo_inicial=pd.Timestamp("2026-01-01"),
            periodo_final=pd.Timestamp("2026-01-01"),
            movimentos=pd.DataFrame(
                {
                    "Competência": [pd.Timestamp("2026-01-01")],
                    "Entradas": [60_000.0],
                    "Saídas": [100_000.0],
                    "Serviços": [0.0],
                }
            ),
        )
        self.lc214 = simulate_lc214_2027_2028(
            revenue=100_000.0,
            rbt12=600_000.0,
            annex="III",
        )

    def test_projection_preserves_2026_inside_rate_and_uses_purchase_credits(self) -> None:
        projection = build_future_projection(
            [self.report],
            self.monthly,
            horizon_months=12,
            average_months=1,
            lc214_simulation=self.lc214,
        )
        row = projection.resumo_anual.iloc[0]
        annual_sales = float(projection.projecao_mensal["Saídas Projetadas"].sum())
        annual_creditable_inputs = float(
            projection.projecao_mensal["Base de Compras Creditável"].sum()
        )

        self.assertAlmostEqual(
            row["DAS Normal · Valor"], annual_sales * 0.08
        )
        self.assertAlmostEqual(row["DAS Normal · Alíquota Efetiva"], 0.08)
        self.assertAlmostEqual(
            row["Crédito Estimado das Compras"],
            annual_creditable_inputs * 0.09,
        )
        self.assertEqual(len(projection.resumo_anual), 2)
        self.assertEqual(projection.resumo_anual["Período"].tolist(), [
            "2027 completo", "2033 estrutural"
        ])

    def test_scenario_table_keeps_2026_and_2027_inside_equal(self) -> None:
        scenarios = scenario_table(self.report, self.lc214)
        inside = scenarios.iloc[1]

        self.assertEqual(inside["Cenário"], "Simples Por Dentro 2027 — mesma carga de 2026")
        self.assertAlmostEqual(inside["Carga Tributária"], 8_000.0)
        self.assertAlmostEqual(inside["Carga Efetiva"], 0.08)
        self.assertAlmostEqual(inside["Variação vs. Atual"], 0.0)
        self.assertEqual(inside["Crédito Estimado das Compras"], 0.0)
        self.assertAlmostEqual(
            scenarios.iloc[2]["Crédito Estimado das Compras"], 60_000.0 * 0.09
        )


if __name__ == "__main__":
    unittest.main()
