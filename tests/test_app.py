import unittest

import pandas as pd

from app import (
    DataValidationError,
    SimulationInputs,
    parse_brazilian_number,
    parse_rate,
    prepare_parameters,
    prepare_transaction_data,
    read_uploaded_table,
    run_simulation,
)


class NumberParsingTests(unittest.TestCase):
    def test_brazilian_and_international_numbers(self) -> None:
        self.assertEqual(parse_brazilian_number("R$ 1.234,56"), 1234.56)
        self.assertEqual(parse_brazilian_number("1234.56"), 1234.56)
        self.assertEqual(parse_brazilian_number("1.234"), 1234.0)
        self.assertEqual(parse_brazilian_number("(10,50)"), -10.5)

    def test_percentage_below_one_percent_is_not_inflated(self) -> None:
        self.assertAlmostEqual(parse_rate("0,10%"), 0.001)
        self.assertAlmostEqual(parse_rate("10,00%"), 0.10)
        self.assertAlmostEqual(parse_rate(0.10), 0.10)


class ImportTests(unittest.TestCase):
    def test_semicolon_csv_with_decimal_commas(self) -> None:
        content = (
            "Data;CFOP;CNPJ_CPF_Cliente;Valor_Total\n"
            "01/01/2027;5102;12.345.678/0001-90;1.234,56\n"
        ).encode("utf-8")
        frame = read_uploaded_table("faturamento.csv", content)
        prepared, _ = prepare_transaction_data(frame, "faturamento")
        self.assertEqual(prepared.loc[0, "Valor_Total"], 1234.56)

    def test_parameter_percentage_string(self) -> None:
        frame = pd.DataFrame(
            {"RBT12": ["1.200.000,00"], "Anexo": ["I"], "Aliquota_Efetiva_Atual": ["0,10%"]}
        )
        _, _, rate, _ = prepare_parameters(frame)
        self.assertAlmostEqual(rate, 0.001)


class SimulationTests(unittest.TestCase):
    @staticmethod
    def make_inputs(input_credit_base: float = 10_000.0) -> SimulationInputs:
        sales_raw = pd.DataFrame(
            {
                "Data": ["01/01/2027", "02/01/2027", "03/01/2027"],
                "CFOP": ["5102", "5102", "1202"],
                "CNPJ_CPF_Cliente": ["12.345.678/0001-90", "123.456.789-00", "123.456.789-00"],
                "Valor_Total": [1_000.0, 500.0, -100.0],
            }
        )
        inputs_raw = pd.DataFrame(
            {
                "Data": ["01/01/2027"],
                "CFOP": ["1102"],
                "Valor_Total_Nota": [input_credit_base],
                "Valor_Base_Credito": [input_credit_base],
            }
        )
        sales, _ = prepare_transaction_data(sales_raw, "faturamento")
        purchases, _ = prepare_transaction_data(inputs_raw, "entradas")
        return SimulationInputs(
            faturamento=sales,
            entradas=purchases,
            rbt12=1_200_000.0,
            anexo="I",
            aliquota_efetiva=0.10,
            aliquota_referencia=0.265,
            fracao_ibs_cbs_das=0.35,
            perda_contratos_percentual=0.05,
            margem_contribuicao=0.30,
        )

    def test_b2b_mix_ignores_returns_in_commercial_denominator(self) -> None:
        result = run_simulation(self.make_inputs())
        self.assertEqual(result.faturamento_total, 1400.0)
        self.assertEqual(result.faturamento_positivo_classificacao, 1500.0)
        self.assertAlmostEqual(result.percentual_b2b, 2 / 3)

    def test_excess_credit_is_reported_as_balance(self) -> None:
        result = run_simulation(self.make_inputs())
        self.assertGreater(result.cenario_2_saldo_creditos, 0)
        self.assertEqual(
            result.cenario_2_creditos_disponiveis,
            result.cenario_2_creditos_utilizados + result.cenario_2_saldo_creditos,
        )
        self.assertGreaterEqual(result.cenario_2_ibs_cbs_liquido, 0)

    def test_invalid_rate_is_rejected(self) -> None:
        inputs = self.make_inputs()
        invalid = SimulationInputs(**{**inputs.__dict__, "aliquota_referencia": 1.01})
        with self.assertRaises(DataValidationError):
            run_simulation(invalid)


if __name__ == "__main__":
    unittest.main()
