import unittest

from simples_lc214 import (
    SimplesLC214Error,
    simulate_lc214_2027_2028,
    tax_comparison_frame,
)


class SimplesLC214Tests(unittest.TestCase):
    def test_all_annexes_and_brackets_reconcile_to_effective_rate(self) -> None:
        representative_rbt12 = (100_000.0, 250_000.0, 500_000.0, 1_000_000.0, 3_000_000.0, 4_000_000.0)
        for annex in ("I", "II", "III", "IV", "V"):
            for rbt12 in representative_rbt12:
                with self.subTest(annex=annex, rbt12=rbt12):
                    result = simulate_lc214_2027_2028(100_000.0, rbt12, annex)
                    self.assertAlmostEqual(result.inside_total, 100_000.0 * result.effective_rate)

    def test_annex_i_uses_2027_2028_rate_and_partition(self) -> None:
        result = simulate_lc214_2027_2028(
            revenue=100_000.0,
            rbt12=1_200_000.0,
            annex="I",
            regular_cbs=7_000.0,
            regular_ibs=2_000.0,
        )
        self.assertEqual(result.bracket, 4)
        self.assertAlmostEqual(result.effective_rate, 0.08825)
        self.assertAlmostEqual(result.inside_total, 8_825.0)
        self.assertAlmostEqual(result.inside_taxes["CBS"], 1_352.8725)
        self.assertAlmostEqual(result.inside_taxes["IBS"], 15.0025)
        self.assertAlmostEqual(
            result.outside_total,
            result.inside_total - result.inside_taxes["CBS"] - result.inside_taxes["IBS"] + 9_000.0,
        )

    def test_iss_cap_is_applied_to_annexes_iii_and_iv(self) -> None:
        annex_iii = simulate_lc214_2027_2028(100_000.0, 3_000_000.0, "III")
        annex_iv = simulate_lc214_2027_2028(100_000.0, 3_000_000.0, "IV")
        self.assertAlmostEqual(annex_iii.inside_taxes["ISS"], 5_000.0)
        self.assertAlmostEqual(annex_iv.inside_taxes["ISS"], 5_000.0)
        self.assertAlmostEqual(annex_iii.inside_total, 100_000.0 * annex_iii.effective_rate)
        self.assertAlmostEqual(annex_iv.inside_total, 100_000.0 * annex_iv.effective_rate)

    def test_comparison_keeps_pgdas_tax_detail(self) -> None:
        result = simulate_lc214_2027_2028(50_000.0, 600_000.0, "V", 3_000.0, 500.0)
        frame = tax_comparison_frame(
            {"IRPJ": 100.0, "INSS/CPP": 800.0, "PIS/Pasep": 90.0, "COFINS": 400.0, "Total": 1_390.0},
            result,
        )
        self.assertEqual(frame.iloc[-1]["Tributo"], "Total")
        self.assertEqual(frame.loc[frame["Tributo"] == "CPP", "Atual (PGDAS/Domínio)"].iloc[0], 800.0)
        self.assertEqual(frame.loc[frame["Tributo"] == "CBS", "2027 Por Fora (DAS + regular)"].iloc[0], 3_000.0)
        self.assertAlmostEqual(frame.iloc[-1]["2027/2028 Por Dentro (DAS)"], result.inside_total)

    def test_comparison_can_preserve_current_inside_total_and_reported_residual(self) -> None:
        result = simulate_lc214_2027_2028(50_000.0, 600_000.0, "II", 3_000.0, 500.0)
        frame = tax_comparison_frame(
            {"IRPJ": 100.0, "IPI": 80.0, "Total": 4_000.0},
            result,
            inside_total_override=4_000.0,
            outside_residual_total_override=1_500.0,
        )

        total = frame.iloc[-1]
        self.assertAlmostEqual(total["Atual (PGDAS/Domínio)"], 4_000.0)
        self.assertAlmostEqual(total["2027/2028 Por Dentro (DAS)"], 4_000.0)
        self.assertAlmostEqual(total["2027 Por Fora (DAS + regular)"], 5_000.0)
        self.assertGreater(
            frame.loc[frame["Tributo"] == "IPI", "2027/2028 Por Dentro (DAS)"].iloc[0],
            0.0,
        )

    def test_rejects_revenue_above_simples_limit(self) -> None:
        with self.assertRaises(SimplesLC214Error):
            simulate_lc214_2027_2028(10_000.0, 4_800_000.01, "I")


if __name__ == "__main__":
    unittest.main()
