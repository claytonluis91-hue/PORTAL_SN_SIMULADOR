"""Simulação das tabelas do Simples Nacional introduzidas pela LC 214/2025.

As tabelas abaixo reproduzem a vigência de 01/01/2027 a 31/12/2028 dos
Anexos XVIII a XXII da LC 214/2025 (Anexos I a V da LC 123/2006).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


class SimplesLC214Error(ValueError):
    """Premissa incompatível com as tabelas legais implementadas."""


BRACKET_LIMITS = (180_000.0, 360_000.0, 720_000.0, 1_800_000.0, 3_600_000.0, 4_800_000.0)


TABLES_2027_2028 = {
    "I": {
        "rates": (0.04, 0.073, 0.095, 0.107, 0.143, 0.189),
        "deductions": (0.0, 5_940.0, 13_860.0, 22_500.0, 87_300.0, 378_000.0),
        "shares": (
            {"IRPJ": .055, "CSLL": .035, "CBS": .1533, "CPP": .415, "ICMS": .34, "IBS": .0017},
            {"IRPJ": .055, "CSLL": .035, "CBS": .1533, "CPP": .415, "ICMS": .34, "IBS": .0017},
            {"IRPJ": .055, "CSLL": .035, "CBS": .1533, "CPP": .42, "ICMS": .335, "IBS": .0017},
            {"IRPJ": .055, "CSLL": .035, "CBS": .1533, "CPP": .42, "ICMS": .335, "IBS": .0017},
            {"IRPJ": .055, "CSLL": .035, "CBS": .1533, "CPP": .42, "ICMS": .335, "IBS": .0017},
            {"IRPJ": .1358, "CSLL": .1006, "CBS": .3402, "CPP": .4234},
        ),
    },
    "II": {
        "rates": (.045, .078, .10, .112, .147, .299),
        "deductions": (0.0, 5_940.0, 13_860.0, 22_500.0, 85_500.0, 720_000.0),
        "shares": tuple(
            {"IRPJ": .055, "CSLL": .035, "CBS": .1385, "CPP": .375, "IPI": .075, "ICMS": .32, "IBS": .0015}
            for _ in range(5)
        ) + ({"IRPJ": .0853, "CSLL": .0753, "CBS": .2522, "CPP": .2359, "IPI": .3513},),
    },
    "III": {
        "rates": (.06, .112, .135, .16, .21, .329),
        "deductions": (0.0, 9_360.0, 17_640.0, 35_640.0, 125_640.0, 648_000.0),
        "shares": (
            {"IRPJ": .04, "CSLL": .035, "CBS": .1543, "CPP": .434, "ISS": .335, "IBS": .0017},
            {"IRPJ": .04, "CSLL": .035, "CBS": .1691, "CPP": .434, "ISS": .32, "IBS": .0019},
            {"IRPJ": .04, "CSLL": .035, "CBS": .1642, "CPP": .434, "ISS": .325, "IBS": .0019},
            {"IRPJ": .04, "CSLL": .035, "CBS": .1642, "CPP": .434, "ISS": .325, "IBS": .0019},
            {"IRPJ": .04, "CSLL": .035, "CBS": .1543, "CPP": .434, "ISS": .335, "IBS": .0017},
            {"IRPJ": .3509, "CSLL": .1504, "CBS": .1929, "CPP": .3058},
        ),
    },
    "IV": {
        "rates": (.045, .09, .102, .14, .22, .329),
        "deductions": (0.0, 8_100.0, 12_420.0, 39_780.0, 183_780.0, 828_000.0),
        "shares": (
            {"IRPJ": .188, "CSLL": .152, "CBS": .2126, "ISS": .445, "IBS": .0024},
            {"IRPJ": .198, "CSLL": .152, "CBS": .2473, "ISS": .40, "IBS": .0027},
            {"IRPJ": .208, "CSLL": .152, "CBS": .2374, "ISS": .40, "IBS": .0026},
            {"IRPJ": .178, "CSLL": .192, "CBS": .2275, "ISS": .40, "IBS": .0025},
            {"IRPJ": .188, "CSLL": .192, "CBS": .2176, "ISS": .40, "IBS": .0024},
            {"IRPJ": .5371, "CSLL": .2159, "CBS": .247},
        ),
    },
    "V": {
        "rates": (.155, .18, .195, .205, .23, .304),
        "deductions": (0.0, 4_500.0, 9_900.0, 17_100.0, 62_100.0, 540_000.0),
        "shares": (
            {"IRPJ": .25, "CSLL": .15, "CBS": .1696, "CPP": .2885, "ISS": .14, "IBS": .0019},
            {"IRPJ": .23, "CSLL": .15, "CBS": .1696, "CPP": .2785, "ISS": .17, "IBS": .0019},
            {"IRPJ": .24, "CSLL": .15, "CBS": .1795, "CPP": .2385, "ISS": .19, "IBS": .002},
            {"IRPJ": .21, "CSLL": .15, "CBS": .1894, "CPP": .2385, "ISS": .21, "IBS": .0021},
            {"IRPJ": .23, "CSLL": .125, "CBS": .1696, "CPP": .2385, "ISS": .235, "IBS": .0019},
            {"IRPJ": .351, "CSLL": .1554, "CBS": .1978, "CPP": .2958},
        ),
    },
}


@dataclass(frozen=True)
class SimplesLC214Simulation:
    annex: str
    bracket: int
    rbt12: float
    revenue: float
    nominal_rate: float
    deduction: float
    effective_rate: float
    inside_taxes: dict[str, float]
    outside_taxes: dict[str, float]

    @property
    def inside_total(self) -> float:
        return sum(self.inside_taxes.values())

    @property
    def outside_total(self) -> float:
        return sum(self.outside_taxes.values())


def _normalize_annex(value: str) -> str:
    annex = str(value).strip().upper().replace("ANEXO", "").strip()
    if annex not in TABLES_2027_2028:
        raise SimplesLC214Error("O Anexo deve ser I, II, III, IV ou V.")
    return annex


def _bracket_index(rbt12: float) -> int:
    if rbt12 <= 0:
        raise SimplesLC214Error("O RBT12 deve ser maior que zero.")
    for index, limit in enumerate(BRACKET_LIMITS):
        if rbt12 <= limit:
            return index
    raise SimplesLC214Error("O RBT12 excede o limite de R$ 4.800.000,00 do Simples Nacional.")


def _component_rates(annex: str, bracket: int, effective_rate: float) -> dict[str, float]:
    # Limites especiais de ISS previstos nas próprias tabelas dos Anexos III e IV.
    if annex == "III" and bracket == 4 and effective_rate > 0.1492537:
        remaining = effective_rate - 0.05
        return {
            "IRPJ": remaining * .0602,
            "CSLL": remaining * .0526,
            "CBS": remaining * .232,
            "CPP": remaining * .6526,
            "ISS": .05,
            "IBS": remaining * .0026,
        }
    if annex == "IV" and bracket == 4 and effective_rate > 0.125:
        remaining = effective_rate - 0.05
        return {
            "IRPJ": remaining * .3133,
            "CSLL": remaining * .32,
            "CBS": remaining * .3627,
            "ISS": .05,
            "IBS": remaining * .004,
        }
    shares = TABLES_2027_2028[annex]["shares"][bracket]
    # Algumas linhas publicadas somam 99,99% ou 100,01% por arredondamento a
    # duas casas. A normalização preserva a proporção legal e reconcilia o DAS.
    share_total = sum(shares.values())
    return {
        tax: effective_rate * share / share_total
        for tax, share in shares.items()
    }


def simulate_lc214_2027_2028(
    revenue: float,
    rbt12: float,
    annex: str,
    regular_cbs: float = 0.0,
    regular_ibs: float = 0.0,
) -> SimplesLC214Simulation:
    """Calcula o DAS por dentro e o DAS residual + IBS/CBS regulares por fora."""
    annex = _normalize_annex(annex)
    if revenue < 0 or regular_cbs < 0 or regular_ibs < 0:
        raise SimplesLC214Error("Receita, CBS e IBS não podem ser negativos.")
    bracket = _bracket_index(float(rbt12))
    table = TABLES_2027_2028[annex]
    nominal_rate = table["rates"][bracket]
    deduction = table["deductions"][bracket]
    effective_rate = (rbt12 * nominal_rate - deduction) / rbt12
    component_rates = _component_rates(annex, bracket, effective_rate)
    inside = {tax: revenue * rate for tax, rate in component_rates.items()}
    outside = {
        tax: value for tax, value in inside.items() if tax not in {"CBS", "IBS"}
    }
    outside["CBS"] = float(regular_cbs)
    outside["IBS"] = float(regular_ibs)
    return SimplesLC214Simulation(
        annex=annex,
        bracket=bracket + 1,
        rbt12=float(rbt12),
        revenue=float(revenue),
        nominal_rate=nominal_rate,
        deduction=deduction,
        effective_rate=effective_rate,
        inside_taxes=inside,
        outside_taxes=outside,
    )


def tax_comparison_frame(
    current_taxes: dict[str, float],
    simulation: SimplesLC214Simulation,
    inside_total_override: float | None = None,
    outside_residual_total_override: float | None = None,
) -> pd.DataFrame:
    """Monta a abertura comparativa no mesmo estilo da composição do PGDAS."""
    current = dict(current_taxes)
    current["CPP"] = current.get("CPP", current.get("INSS/CPP", 0.0))
    inside = dict(simulation.inside_taxes)
    if inside_total_override is not None and simulation.inside_total > 0:
        inside_factor = inside_total_override / simulation.inside_total
        inside = {tax: value * inside_factor for tax, value in inside.items()}

    outside = dict(simulation.outside_taxes)
    if outside_residual_total_override is not None:
        residual_taxes = {tax: value for tax, value in inside.items() if tax not in {"CBS", "IBS"}}
        residual_total = sum(residual_taxes.values())
        residual_factor = outside_residual_total_override / residual_total if residual_total else 0.0
        outside = {tax: value * residual_factor for tax, value in residual_taxes.items()}
        outside["CBS"] = simulation.outside_taxes.get("CBS", 0.0)
        outside["IBS"] = simulation.outside_taxes.get("IBS", 0.0)

    order = ["IRPJ", "CSLL", "CPP", "IPI", "ICMS", "ISS", "PIS/Pasep", "COFINS", "CBS", "IBS"]
    rows = [
        {
            "Tributo": tax,
            "Atual (PGDAS/Domínio)": float(current.get(tax, 0.0)),
            "2027/2028 Por Dentro (DAS)": float(inside.get(tax, 0.0)),
            "2027 Por Fora (DAS + regular)": float(outside.get(tax, 0.0)),
            "Recolhimento no Por Fora": "Regime regular" if tax in {"CBS", "IBS"} else "DAS residual",
        }
        for tax in order
    ]
    rows.append(
        {
            "Tributo": "Total",
            "Atual (PGDAS/Domínio)": float(current.get("Total", sum(row["Atual (PGDAS/Domínio)"] for row in rows))),
            "2027/2028 Por Dentro (DAS)": float(sum(inside.values())),
            "2027 Por Fora (DAS + regular)": float(sum(outside.values())),
            "Recolhimento no Por Fora": "DAS residual + CBS + IBS",
        }
    )
    return pd.DataFrame(rows)
