"""Enriquecimento cadastral indicativo por CNPJ, CNAE, LC 116 e NBS."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests


BRASIL_API_CNPJ_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
NBS_DATA_VERSION = "2026.07"
NBS_REQUIRED_FILES = (
    "AnexoVIII_Convertido.json",
    "classificacao_tributaria.json",
    "lista_servicos_completa.json",
)


class BusinessActivityError(ValueError):
    """Falha amigável de consulta ou classificação cadastral."""


def normalize_cnae(value: Any) -> str:
    digits = re.sub(r"\D", "", "" if value is None else str(value))
    return digits.zfill(7) if digits else ""


def format_cnae(value: Any) -> str:
    digits = normalize_cnae(value)
    return f"{digits[:4]}-{digits[4]}/{digits[5:]}" if len(digits) == 7 else digits


def normalize_service_code(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip().replace(",", ".")
    match = re.search(r"(\d+)\s*\.\s*(\d+)", text)
    if not match:
        digits = re.sub(r"\D", "", text)
        return str(int(digits)) if digits else ""
    group = str(int(match.group(1)))
    item = match.group(2)[:2].ljust(2, "0")
    return f"{group}.{item}"


def normalize_tax_class(value: Any) -> str:
    digits = re.sub(r"\D", "", "" if value is None else str(value))
    return digits.zfill(6) if digits else ""


def validate_cnpj(value: Any) -> bool:
    cnpj = re.sub(r"\D", "", str(value or ""))
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False

    def check_digit(base: str, weights: list[int]) -> str:
        remainder = sum(int(number) * weight for number, weight in zip(base, weights)) % 11
        return "0" if remainder < 2 else str(11 - remainder)

    first = check_digit(cnpj[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = check_digit(cnpj[:12] + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return cnpj[-2:] == first + second


def query_company_by_cnpj(cnpj_value: str) -> dict[str, Any]:
    """Consulta cadastral sob demanda usando o endpoint público da BrasilAPI."""
    cnpj = re.sub(r"\D", "", cnpj_value or "")
    if not validate_cnpj(cnpj):
        raise BusinessActivityError("CNPJ inválido. Confira os 14 dígitos e os verificadores.")
    try:
        response = requests.get(
            BRASIL_API_CNPJ_URL.format(cnpj=cnpj),
            timeout=(3.05, 8),
            headers={"Accept": "application/json", "User-Agent": "Portal-Nascel-IBS-CBS/1.0"},
        )
    except requests.RequestException as exc:
        raise BusinessActivityError("Não foi possível acessar a consulta pública de CNPJ.") from exc
    if response.status_code == 404:
        raise BusinessActivityError("CNPJ não encontrado na fonte pública consultada.")
    if response.status_code != 200:
        raise BusinessActivityError(f"A consulta pública de CNPJ respondeu com status {response.status_code}.")
    try:
        data = response.json()
    except ValueError as exc:
        raise BusinessActivityError("A fonte pública retornou uma resposta inválida.") from exc
    if not isinstance(data, dict):
        raise BusinessActivityError("A fonte pública retornou um formato inesperado.")
    data["fonte_dados"] = "BrasilAPI / Minha Receita"
    data.setdefault("cnpj", cnpj)
    return data


def extract_company_activities(data: dict[str, Any]) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []

    def add(code: Any, description: Any = "", *, primary: bool = False) -> None:
        normalized = normalize_cnae(code)
        if not normalized or any(item["CNAE"] == normalized for item in activities):
            return
        activities.append(
            {
                "CNAE": normalized,
                "CNAE formatado": format_cnae(normalized),
                "Atividade": str(description or "").strip(),
                "Tipo": "Principal" if primary else "Secundária",
            }
        )

    add(data.get("cnae_fiscal"), data.get("cnae_fiscal_descricao"), primary=True)
    principal = data.get("cnae_fiscal_principal")
    if isinstance(principal, dict):
        add(
            principal.get("codigo") or principal.get("code"),
            principal.get("descricao") or principal.get("text"),
            primary=True,
        )
    for item in data.get("atividade_principal") or []:
        if isinstance(item, dict):
            add(item.get("codigo") or item.get("code"), item.get("descricao") or item.get("text"), primary=True)
    for item in data.get("cnaes_secundarios") or data.get("atividades_secundarias") or []:
        if isinstance(item, dict):
            add(
                item.get("codigo") or item.get("cnae_fiscal") or item.get("code"),
                item.get("descricao") or item.get("text"),
            )
    return activities


def company_summary(data: dict[str, Any]) -> dict[str, str]:
    return {
        "Razão social": str(data.get("razao_social") or data.get("nome") or "Não informada"),
        "Nome fantasia": str(data.get("nome_fantasia") or data.get("fantasia") or "Não informado"),
        "Situação cadastral": str(
            data.get("descricao_situacao_cadastral") or data.get("situacao_cadastral") or data.get("situacao") or "Não informada"
        ),
        "Fonte": str(data.get("fonte_dados") or "BrasilAPI / Minha Receita"),
    }


def discover_nbs_data_dir(explicit: str | Path | None = None) -> Path | None:
    candidates = [
        Path(explicit) if explicit else None,
        Path(os.environ["NASCEL_NBS_DATA_DIR"]) if os.getenv("NASCEL_NBS_DATA_DIR") else None,
        Path(__file__).resolve().parent.parent / "AUDITORIA-NBS - v2",
    ]
    return next(
        (
            path
            for path in candidates
            if path is not None and all((path / name).is_file() for name in NBS_REQUIRED_FILES)
        ),
        None,
    )


def _read_json_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise BusinessActivityError(f"Base auxiliar inválida: {path.name}.")
    return data


def service_tax_candidates(
    cnaes: Iterable[Any], data_dir: str | Path | None = None
) -> pd.DataFrame:
    """Gera candidatos CNAE → LC 116 → NBS → cClassTrib e redução."""
    directory = discover_nbs_data_dir(data_dir)
    if directory is None:
        raise BusinessActivityError(
            "As bases da Auditoria NBS não foram localizadas. Configure NASCEL_NBS_DATA_DIR para habilitar a classificação."
        )
    requested = {normalize_cnae(value) for value in cnaes if normalize_cnae(value)}
    columns = [
        "CNAE", "Atividade", "Item LC 116", "NBS", "Descrição NBS", "cClassTrib",
        "Classificação tributária", "Redução IBS (%)", "Redução CBS (%)", "Local de incidência IBS",
    ]
    if not requested:
        return pd.DataFrame(columns=columns)

    cnae_records = _read_json_records(directory / "lista_servicos_completa.json")
    main_records = _read_json_records(directory / "AnexoVIII_Convertido.json")
    rule_records = _read_json_records(directory / "classificacao_tributaria.json")
    rules = {
        normalize_tax_class(item.get("Código da Classificação Tributária")): item
        for item in rule_records
    }
    services_by_item: dict[str, list[dict[str, Any]]] = {}
    for item in main_records:
        services_by_item.setdefault(normalize_service_code(item.get("Item LC 116")), []).append(item)

    rows: list[dict[str, Any]] = []
    for link in cnae_records:
        cnae = normalize_cnae(link.get("cnae"))
        if cnae not in requested:
            continue
        service_item = normalize_service_code(link.get("descricao_item"))
        activity = str(link.get("item_lista_servico") or link.get("descricao_cnae") or "").strip()
        for candidate in services_by_item.get(service_item, []):
            tax_class = normalize_tax_class(candidate.get("cClassTrib"))
            rule = rules.get(tax_class, {})
            rows.append(
                {
                    "CNAE": format_cnae(cnae),
                    "Atividade": activity,
                    "Item LC 116": service_item,
                    "NBS": str(candidate.get("NBS") or "Não localizada"),
                    "Descrição NBS": str(candidate.get("DESCRIÇÃO NBS") or ""),
                    "cClassTrib": tax_class,
                    "Classificação tributária": str(candidate.get("nome cClassTrib") or ""),
                    "Redução IBS (%)": float(rule.get("Percentual Redução IBS") or 0),
                    "Redução CBS (%)": float(rule.get("Percentual Redução CBS") or 0),
                    "Local de incidência IBS": str(candidate.get("Local incidência IBS") or ""),
                }
            )
    return pd.DataFrame(rows, columns=columns).drop_duplicates().reset_index(drop=True)
