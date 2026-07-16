import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from business_activity import (
    company_summary,
    extract_company_activities,
    query_company_by_cnpj,
    service_tax_candidates,
    validate_cnpj,
)


class CNPJLookupTests(unittest.TestCase):
    def test_validates_cnpj_and_extracts_activities(self) -> None:
        self.assertTrue(validate_cnpj("15.435.515/0001-91"))
        activities = extract_company_activities(
            {
                "cnae_fiscal": 6201501,
                "cnae_fiscal_descricao": "Desenvolvimento de software",
                "cnaes_secundarios": [{"codigo": 6202300, "descricao": "Licenciamento"}],
            }
        )
        self.assertEqual([item["CNAE"] for item in activities], ["6201501", "6202300"])
        self.assertEqual(activities[0]["Tipo"], "Principal")

    @patch("business_activity.requests.get")
    def test_queries_brasil_api_on_demand(self, get: MagicMock) -> None:
        response = MagicMock(status_code=200)
        response.json.return_value = {"razao_social": "EMPRESA TESTE", "cnae_fiscal": 6201501}
        get.return_value = response

        result = query_company_by_cnpj("15435515000191")

        self.assertEqual(company_summary(result)["Razão social"], "EMPRESA TESTE")
        self.assertEqual(result["fonte_dados"], "BrasilAPI / Minha Receita")
        get.assert_called_once()


class ServiceTaxCandidateTests(unittest.TestCase):
    def test_crosses_cnae_nbs_and_reduction_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixtures = {
                "lista_servicos_completa.json": [
                    {
                        "cnae": "6201501",
                        "item_lista_servico": "Desenvolvimento de software",
                        "descricao_item": "01,01",
                    }
                ],
                "AnexoVIII_Convertido.json": [
                    {
                        "Item LC 116": 1.01,
                        "NBS": "1.1502.20.00",
                        "DESCRIÇÃO NBS": "Serviço de desenvolvimento",
                        "cClassTrib": "200001",
                        "nome cClassTrib": "Tratamento diferenciado candidato",
                        "Local incidência IBS": "Domicílio do adquirente",
                    }
                ],
                "classificacao_tributaria.json": [
                    {
                        "Código da Classificação Tributária": "200001",
                        "Percentual Redução IBS": "60",
                        "Percentual Redução CBS": "60",
                    }
                ],
            }
            for name, data in fixtures.items():
                (directory / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            candidates = service_tax_candidates(["6201-5/01"], directory)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates.iloc[0]["cClassTrib"], "200001")
        self.assertEqual(candidates.iloc[0]["Redução IBS (%)"], 60.0)
        self.assertEqual(candidates.iloc[0]["Redução CBS (%)"], 60.0)


if __name__ == "__main__":
    unittest.main()
