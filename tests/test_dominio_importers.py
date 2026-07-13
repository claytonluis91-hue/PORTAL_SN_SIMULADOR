import unittest
from io import BytesIO
from pathlib import Path

import pandas as pd

from analytics_engine import build_future_projection, generate_local_intelligent_report
from app import prepare_parameters, prepare_transaction_data
from dashboard_exports import (
    build_excel_dashboard,
    build_transactional_template,
    create_dashboard_images,
)
from dominio_importers import (
    cnpjs_are_compatible,
    parse_dominio_monthly,
    parse_dominio_simulation,
    parse_pgdas,
)


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ROOT / "documentos"


class DominioImporterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        required = [
            DOCUMENTS / "052026 - Resumido.xls",
            DOCUMENTS / "Demonstrativo Mensal.xls",
            DOCUMENTS / "PGDAS_TESTE.pdf",
        ]
        if not all(path.exists() for path in required):
            raise unittest.SkipTest("Arquivos fiscais locais não são versionados no repositório.")
        cls.report = parse_dominio_simulation((DOCUMENTS / "052026 - Resumido.xls").read_bytes())
        cls.monthly = parse_dominio_monthly((DOCUMENTS / "Demonstrativo Mensal.xls").read_bytes())
        cls.pgdas = parse_pgdas((DOCUMENTS / "PGDAS_TESTE.pdf").read_bytes())

    def test_dominio_summary_values(self) -> None:
        self.assertEqual(self.report.cnpj, "15435515000191")
        self.assertAlmostEqual(self.report.base_saidas, 51960.81)
        self.assertAlmostEqual(self.report.base_entradas_credito, 44569.10)
        self.assertAlmostEqual(self.report.tributos_atuais["Total"], 4460.32)
        self.assertAlmostEqual(self.report.fase_2027["total"], 4486.35)
        self.assertAlmostEqual(self.report.fase_2033["total"], 4367.37)
        self.assertAlmostEqual(self.report.percentual_operacoes_creditaveis, 0.9325027843)

    def test_monthly_report_has_all_competences(self) -> None:
        self.assertEqual(len(self.monthly.movimentos), 17)
        may = self.monthly.movimentos[
            self.monthly.movimentos["Competência"] == pd.Timestamp("2026-05-01")
        ].iloc[0]
        self.assertAlmostEqual(may["Entradas"], 45371.55)
        self.assertAlmostEqual(may["Saídas"], 51960.81)

    def test_pgdas_is_parsed_but_not_merged_with_other_cnpj(self) -> None:
        self.assertEqual(self.pgdas.anexo, "III")
        self.assertAlmostEqual(self.pgdas.rbt12, 472775.49)
        self.assertAlmostEqual(self.pgdas.total_das, 3435.50)
        self.assertFalse(cnpjs_are_compatible(self.report.cnpj, self.pgdas))

    def test_excel_and_png_exports(self) -> None:
        projection = build_future_projection([self.report], self.monthly, 12, 12, 0.0)
        intelligent_report = generate_local_intelligent_report(
            projection, [self.report], self.monthly, self.pgdas
        )
        excel = build_excel_dashboard(
            self.report,
            self.monthly,
            self.pgdas,
            projection=projection,
            intelligent_report=intelligent_report,
            reports=[self.report],
        )
        self.assertEqual(excel[:2], b"PK")
        self.assertGreater(len(excel), 50_000)
        images = create_dashboard_images(self.report, self.monthly, self.pgdas, projection)
        self.assertEqual(
            set(images),
            {
                "dashboard_cenarios.png",
                "dashboard_evolucao_mensal.png",
                "dashboard_pontos_atencao.png",
                "dashboard_projecao_futura.png",
            },
        )
        self.assertTrue(all(content.startswith(b"\x89PNG") for content in images.values()))

    def test_future_projection_uses_average_and_deduplicates_periods(self) -> None:
        projection = build_future_projection(
            [self.report, self.report], self.monthly, horizon_months=6, average_months=6
        )
        expected_sales = float(
            (self.monthly.movimentos.tail(6)["Saídas"] + self.monthly.movimentos.tail(6)["Serviços"]).mean()
        )
        self.assertAlmostEqual(projection.media_saidas, expected_sales)
        self.assertEqual(len(projection.projecao_mensal), 6)
        self.assertEqual(len(projection.historico_simulacoes), 1)
        self.assertIn("Relatório inteligente", generate_local_intelligent_report(
            projection, [self.report], self.monthly, self.pgdas
        ))

        automatic = build_future_projection(
            [self.report],
            self.monthly,
            horizon_months=6,
            average_months=6,
            growth_mode="average",
            growth_lookback_months=6,
        )
        self.assertEqual(automatic.modo_crescimento, "average")
        self.assertAlmostEqual(automatic.crescimento_anual, -0.1365082427)
        self.assertLess(
            automatic.projecao_mensal.iloc[-1]["Saídas Projetadas"],
            automatic.projecao_mensal.iloc[0]["Saídas Projetadas"],
        )

    def test_transactional_template_has_required_sheets(self) -> None:
        template = build_transactional_template()
        self.assertEqual(template[:2], b"PK")
        excel = pd.ExcelFile(BytesIO(template), engine="calamine")
        self.assertEqual(
            excel.sheet_names,
            ["Instrucoes", "Faturamento", "Entradas", "Parametros_SN"],
        )
        sales = pd.read_excel(BytesIO(template), sheet_name="Faturamento", engine="calamine")
        purchases = pd.read_excel(BytesIO(template), sheet_name="Entradas", engine="calamine")
        parameters = pd.read_excel(BytesIO(template), sheet_name="Parametros_SN", engine="calamine")
        prepared_sales, _ = prepare_transaction_data(sales, "faturamento")
        prepared_purchases, _ = prepare_transaction_data(purchases, "entradas")
        _, annex, rate, _ = prepare_parameters(parameters)
        self.assertEqual(prepared_sales["Valor_Total"].sum(), 10000.0)
        self.assertEqual(prepared_purchases["Valor_Base_Credito"].sum(), 7000.0)
        self.assertEqual(annex, "I")
        self.assertAlmostEqual(rate, 0.085)


if __name__ == "__main__":
    unittest.main()
