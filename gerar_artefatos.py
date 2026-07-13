"""Gera o dashboard Excel e as imagens a partir dos exemplos em documentos/."""

from pathlib import Path

from analytics_engine import build_future_projection, generate_local_intelligent_report
from dashboard_exports import (
    build_excel_dashboard,
    build_transactional_template,
    create_dashboard_images,
)
from dominio_importers import parse_dominio_monthly, parse_dominio_simulation, parse_pgdas


def main() -> None:
    source = Path(__file__).parent / "documentos"
    output = Path(__file__).parent / "resultados"
    output.mkdir(exist_ok=True)

    reports = [
        parse_dominio_simulation(path.read_bytes())
        for path in sorted(source.glob("*Resumido*.xls"))
    ]
    if not reports:
        raise FileNotFoundError("Nenhum arquivo *Resumido*.xls foi encontrado em documentos.")
    dominio = max(reports, key=lambda report: report.periodo)
    monthly = parse_dominio_monthly((source / "Demonstrativo Mensal.xls").read_bytes())
    pgdas = parse_pgdas((source / "PGDAS_TESTE.pdf").read_bytes())
    projection = build_future_projection(
        reports,
        monthly,
        growth_mode="average",
        growth_lookback_months=6,
    )
    intelligent_report = generate_local_intelligent_report(projection, reports, monthly, pgdas)

    workbook = build_excel_dashboard(
        dominio,
        monthly,
        pgdas,
        projection=projection,
        intelligent_report=intelligent_report,
        reports=reports,
    )
    (output / "Dashboard_Reforma_Tributaria_Axigram.xlsx").write_bytes(workbook)
    (output / "Modelo_Importacao_Transacional_IBS_CBS.xlsx").write_bytes(
        build_transactional_template()
    )
    (output / "Relatorio_Inteligente_Axigram.md").write_text(
        intelligent_report, encoding="utf-8"
    )
    for name, content in create_dashboard_images(dominio, monthly, pgdas, projection).items():
        (output / name).write_bytes(content)

    print(f"Artefatos gerados em: {output.resolve()}")


if __name__ == "__main__":
    main()
