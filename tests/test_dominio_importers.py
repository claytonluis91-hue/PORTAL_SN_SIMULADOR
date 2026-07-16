import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pandas as pd

from analytics_engine import (
    build_future_projection,
    generate_local_intelligent_report,
    generate_report_with_gemini,
    list_gemini_models,
)
from app import prepare_parameters, prepare_transaction_data
from dashboard_exports import (
    build_excel_dashboard,
    build_transactional_template,
    create_dashboard_images,
    dashboard_explanations,
)
from dominio_importers import (
    MonthlyReport,
    _parse_dominio_simulation_frames,
    _simulation_sheet_pairs,
    cnpjs_are_compatible,
    parse_dominio_monthly,
    parse_dominio_simulation,
    parse_pgdas,
)
from simples_lc214 import simulate_lc214_2027_2028, tax_comparison_frame
from nascel_consulting import (
    build_nascel_diagnostic,
    decision_matrix_frame,
    legal_timeline_frame,
    official_sources_frame,
)


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ROOT / "documentos"


class ServiceCompanyWithoutInputsTests(unittest.TestCase):
    @staticmethod
    def simulation_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
        summary = pd.DataFrame(None, index=range(20), columns=range(75), dtype=object)
        summary.iat[0, 0] = "CD SERVIÇOS LTDA"
        summary.iat[1, 4] = "12.345.678/0001-95"
        summary.iat[2, 4] = pd.Timestamp("2026-01-01")

        summary.iat[4, 0] = "2027 - 1ª fase"
        summary.iat[10, 0] = "2033 - fase final"
        for row, total, difference in ((5, 92.0, 12.0), (11, 95.0, 15.0)):
            summary.iat[row, 38] = 80.0
            summary.iat[row, 41] = 20.0
            summary.iat[row, 48] = 45.0
            summary.iat[row, 52] = total - 65.0
            summary.iat[row, 58] = total
            summary.iat[row, 68] = difference
            summary.iat[row, 74] = difference / 10
        summary.iat[11, 1] = 5.0
        summary.iat[11, 6] = 5.0
        summary.iat[11, 10] = 20.0
        summary.iat[11, 12] = 0.0
        summary.iat[11, 20] = 0.0
        summary.iat[11, 24] = 30.0
        summary.iat[11, 30] = 5.0
        summary.iat[11, 34] = 15.0
        summary.iat[15, 0] = "Débitos pelas Saídas"
        summary.iat[15, 8] = 1_000.0
        summary.iat[15, 14] = 8.8
        summary.iat[15, 22] = 0.0
        summary.iat[15, 50] = 8.8
        summary.iat[15, 60] = 17.7

        detail = pd.DataFrame(None, index=range(14), columns=range(17), dtype=object)
        detail.iat[6, 1] = "Serviços tributados"
        detail.iat[6, 13] = 1_000.0
        detail.iat[7, 0] = "Clientes"
        detail.iat[8, 1] = "Regime regular"
        detail.iat[8, 13] = 1_000.0
        detail.iat[13, 0] = "Total"
        return summary, detail

    def test_simulation_accepts_omitted_input_sections(self) -> None:
        summary, detail = self.simulation_frames()
        report = _parse_dominio_simulation_frames(summary, detail)

        self.assertEqual(report.base_entradas_credito, 0.0)
        self.assertTrue(report.entradas_por_acumulador.empty)
        self.assertTrue(report.fornecedores_por_regime.empty)
        self.assertEqual(
            report.entradas_por_acumulador.columns.tolist(),
            ["Descrição", "Valor", "Percentual"],
        )
        self.assertEqual(report.aliquota_credito_cbs_2027, 0.0)
        self.assertEqual(report.aliquota_credito_ibs_2033, 0.0)

    def test_monthly_report_accepts_missing_input_column(self) -> None:
        frame = pd.DataFrame(None, index=range(12), columns=range(25), dtype=object)
        frame.iat[0, 4] = "CD SERVIÇOS LTDA"
        frame.iat[3, 4] = "12.345.678/0001-95"
        frame.iat[5, 4] = pd.Timestamp("2026-01-01")
        frame.iat[5, 13] = pd.Timestamp("2026-01-31")
        frame.iat[9, 0] = "Mês"
        frame.iat[9, 6] = "Ano"
        frame.iat[9, 15] = "Saídas R$"
        frame.iat[9, 22] = "Serviços R$"
        frame.iat[10, 0] = "Janeiro"
        frame.iat[10, 5] = 2026
        frame.iat[10, 15] = 0.0
        frame.iat[10, 22] = 1_000.0
        frame.iat[11, 0] = "Totais"
        workbook = MagicMock(sheet_names=["Demonstrativo"])

        with patch("dominio_importers._open_legacy_workbook", return_value=workbook), patch(
            "dominio_importers._read_sheet", return_value=frame
        ):
            monthly = parse_dominio_monthly(b"service-company")

        self.assertEqual(monthly.movimentos.iloc[0]["Entradas"], 0.0)
        self.assertEqual(monthly.movimentos.iloc[0]["Serviços"], 1_000.0)

    def test_projection_keeps_input_credits_at_zero(self) -> None:
        summary, detail = self.simulation_frames()
        report = _parse_dominio_simulation_frames(summary, detail)
        monthly = MonthlyReport(
            empresa=report.empresa,
            cnpj=report.cnpj,
            periodo_inicial=pd.Timestamp("2026-01-01"),
            periodo_final=pd.Timestamp("2026-01-31"),
            movimentos=pd.DataFrame(
                [{"Competência": report.periodo, "Entradas": 0.0, "Saídas": 0.0, "Serviços": 1_000.0}]
            ),
        )

        projection = build_future_projection([report], monthly)

        self.assertEqual(projection.media_entradas, 0.0)
        self.assertEqual(projection.percentual_entradas_creditaveis, 0.0)
        self.assertEqual(projection.totais["credito_compras_2027"], 0.0)


class DominioImporterTests(unittest.TestCase):
    def test_multi_month_workbook_pairs_summary_and_detail_tabs(self) -> None:
        workbook = MagicMock()
        workbook.sheet_names = [
            "Resumido 01-2027", "Detalhado 01-2027",
            "Resumido 02-2027", "Detalhado 02-2027",
        ]
        self.assertEqual(
            _simulation_sheet_pairs(workbook),
            [
                ("Resumido 01-2027", "Detalhado 01-2027"),
                ("Resumido 02-2027", "Detalhado 02-2027"),
            ],
        )

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
        self.assertAlmostEqual(self.report.aliquota_credito_cbs_2027, 0.088)
        self.assertAlmostEqual(self.report.aliquota_credito_ibs_2027, 0.0)
        self.assertAlmostEqual(self.report.aliquota_credito_cbs_2033, 0.088)
        self.assertAlmostEqual(self.report.aliquota_credito_ibs_2033, 0.177)

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
        lc214 = simulate_lc214_2027_2028(
            self.report.base_saidas,
            self.pgdas.rbt12,
            self.pgdas.anexo,
            self.report.fase_2027["cbs"],
            self.report.fase_2027["ibs"],
        )
        lc214_comparison = tax_comparison_frame(
            self.report.tributos_atuais,
            lc214,
            inside_total_override=self.report.tributos_atuais["Total"],
            outside_residual_total_override=self.report.fase_2027["simples_residual"],
        )
        excel = build_excel_dashboard(
            self.report,
            self.monthly,
            self.pgdas,
            projection=projection,
            intelligent_report=intelligent_report,
            reports=[self.report],
            lc214_comparison=lc214_comparison,
            lc214_simulation=lc214,
            activity_candidates=pd.DataFrame(
                [
                    {
                        "CNAE": "6201-5/01",
                        "Atividade": "Desenvolvimento de software",
                        "Item LC 116": "1.01",
                        "NBS": "1.1502.20.00",
                        "Descrição NBS": "Serviço candidato",
                        "cClassTrib": "000001",
                        "Classificação tributária": "Tributação integral",
                        "Redução IBS (%)": 0.0,
                        "Redução CBS (%)": 0.0,
                        "Local de incidência IBS": "Domicílio do adquirente",
                    }
                ]
            ),
        )
        self.assertEqual(excel[:2], b"PK")
        self.assertGreater(len(excel), 50_000)
        workbook = pd.ExcelFile(BytesIO(excel), engine="calamine")
        self.assertIn("Simples_LC214", workbook.sheet_names)
        self.assertIn("Como_Ler", workbook.sheet_names)
        self.assertIn("Diagnostico_Nascel", workbook.sheet_names)
        self.assertIn("Decisao_Tributaria", workbook.sheet_names)
        self.assertIn("Cronograma_Legal", workbook.sheet_names)
        self.assertIn("Fontes_Oficiais", workbook.sheet_names)
        self.assertIn("Atividades_CNPJ", workbook.sheet_names)
        sensitivity = pd.read_excel(
            BytesIO(excel), sheet_name="Sensibilidade", header=None, engine="calamine"
        )
        self.assertIn("FINALIDADE", str(sensitivity.iat[1, 0]))
        self.assertEqual(sensitivity.iat[8, 1], "Base Creditável / Receita")
        self.assertIn("Base atual da empresa", sensitivity.iloc[:, 0].astype(str).tolist())
        guide = pd.read_excel(BytesIO(excel), sheet_name="Como_Ler", skiprows=3, engine="calamine")
        self.assertIn("Como interpretar", guide.columns)
        explanations = dashboard_explanations(
            self.report, self.monthly, self.pgdas, projection, lc214
        )
        self.assertIn("Nova tabela LC 214/2025", explanations["Indicador"].tolist())
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

    def test_nascel_consulting_layers_are_explainable(self) -> None:
        lc214 = simulate_lc214_2027_2028(
            self.report.base_saidas,
            self.pgdas.rbt12,
            self.pgdas.anexo,
            self.report.fase_2027["cbs"],
            self.report.fase_2027["ibs"],
        )
        diagnostic = build_nascel_diagnostic(
            self.report, self.monthly, self.pgdas, [self.report], lc214
        )
        self.assertGreaterEqual(diagnostic.score, 0)
        self.assertLessEqual(diagnostic.score, 100)
        self.assertEqual(len(diagnostic.checklist), 6)
        self.assertIn("Próxima ação", diagnostic.checklist.columns)
        matrix = decision_matrix_frame(self.report, lc214)
        self.assertEqual(matrix["Caminho"].tolist(), [
            "CBS/IBS dentro do DAS", "CBS/IBS fora do DAS"
        ])
        self.assertAlmostEqual(matrix.iloc[0]["Desembolso estimado"], 4_460.32)
        self.assertAlmostEqual(matrix.iloc[1]["Desembolso estimado"], 4_486.35)
        self.assertIn("Base legal", matrix.columns)
        self.assertEqual(legal_timeline_frame()["Período"].tolist(), [
            "2026", "2027–2028", "2029–2032", "2033"
        ])
        self.assertTrue(
            official_sources_frame()["URL"].str.startswith("https://").all()
        )

    def test_future_projection_uses_average_and_deduplicates_periods(self) -> None:
        projection = build_future_projection(
            [self.report, self.report], self.monthly, horizon_months=6, average_months=6
        )
        expected_sales = float(
            (self.monthly.movimentos.tail(6)["Saídas"] + self.monthly.movimentos.tail(6)["Serviços"]).mean()
        )
        self.assertAlmostEqual(projection.media_saidas, expected_sales)
        self.assertEqual(len(projection.projecao_mensal), 12)
        self.assertEqual(len(projection.resumo_anual), 2)
        self.assertEqual(len(projection.historico_simulacoes), 1)
        self.assertIn("Relatório Consultivo Nascel", generate_local_intelligent_report(
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

    @patch("google.genai.Client")
    def test_gemini_report_integration_uses_selected_model(self, client_class: MagicMock) -> None:
        projection = build_future_projection([self.report], self.monthly)
        response = MagicMock()
        response.text = "Relatório Gemini validado"
        client_class.return_value.models.generate_content.return_value = response
        result = generate_report_with_gemini(
            "relatório-base", projection, "chave-de-teste", "gemini-3.5-flash"
        )
        self.assertEqual(result, "Relatório Gemini validado")
        client_class.return_value.models.generate_content.assert_called_once_with(
            model="gemini-3.5-flash",
            contents=ANY,
            config=ANY,
        )

    @patch("google.genai.Client")
    def test_gemini_model_list_filters_and_prioritizes_text_models(
        self, client_class: MagicMock
    ) -> None:
        def model(name: str, actions: list[str]) -> MagicMock:
            item = MagicMock()
            item.name = f"models/{name}"
            item.supported_actions = actions
            return item

        client_class.return_value.models.list.return_value = [
            model("gemini-2.5-flash", ["generateContent"]),
            model("text-embedding-004", ["embedContent"]),
            model("gemini-3.5-flash", ["generateContent"]),
            model("gemini-2.5-flash-image", ["generateContent"]),
        ]
        self.assertEqual(
            list_gemini_models("chave-de-teste"),
            ["gemini-3.5-flash", "gemini-2.5-flash"],
        )

    @patch("google.genai.Client")
    def test_gemini_quota_error_is_actionable(self, client_class: MagicMock) -> None:
        projection = build_future_projection([self.report], self.monthly)
        client_class.return_value.models.generate_content.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED quota exceeded"
        )
        with self.assertRaisesRegex(Exception, "cota"):
            generate_report_with_gemini(
                "relatório-base", projection, "chave-de-teste", "gemini-3.5-flash"
            )


if __name__ == "__main__":
    unittest.main()
