@echo off
setlocal

title Portal de Simulacao IBS-CBS
cd /d "%~dp0"

echo ============================================================
echo   Portal de Simulacao da Reforma Tributaria - IBS/CBS
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao foi encontrado no PATH do Windows.
    echo Instale o Python e marque a opcao "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

if not exist "app.py" (
    echo [ERRO] O arquivo app.py nao foi encontrado nesta pasta.
    echo.
    pause
    exit /b 1
)

echo Verificando dependencias...
python -c "import streamlit, pandas, plotly, xlsxwriter, matplotlib, fitz, python_calamine, reportlab, openai" >nul 2>&1
if errorlevel 1 (
    echo Dependencias ausentes. Instalando a partir de requirements.txt...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERRO] Nao foi possivel instalar as dependencias.
        echo Verifique sua conexao com a internet e as permissoes do Python.
        echo.
        pause
        exit /b 1
    )
)

echo.
echo Iniciando o portal em http://localhost:8501
echo Para encerrar, pressione CTRL+C nesta janela.
echo.

python -m streamlit run app.py --server.address localhost --server.port 8501

if errorlevel 1 (
    echo.
    echo [ERRO] O portal foi encerrado devido a uma falha.
    pause
)

endlocal
