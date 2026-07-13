# Portal de Simulação da Reforma Tributária (IBS/CBS)

Dashboard em Streamlit para comparar o Simples Nacional **Por Dentro** com o
modelo **Híbrido**, usando exportações CSV/XLSX do sistema Domínio.

## Execução

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

No Windows, também é possível executar `iniciar_portal.bat` por duplo clique.
O arquivo verifica as dependências e abre o portal em `http://localhost:8501`.

O portal solicita três arquivos: faturamento, entradas e parâmetros do Simples.
As alíquotas de referência, a participação de IBS/CBS na alíquota do DAS, a
receita de contratos em risco e a margem de contribuição são premissas
editáveis na barra lateral. A alíquota combinada de 26,5% representa um cenário
estrutural simplificado, não uma aplicação automática do cronograma anual de
transição.

## Importação direta dos relatórios do Domínio

O modo **Relatórios consolidados do Domínio + PGDAS** reconhece diretamente:

- um ou mais arquivos `Simulação Reforma Tributária - Resumido.xls`, incluindo
  as abas Resumido e Detalhado de cada competência;
- `Demonstrativo Mensal.xls`;
- extrato do `PGDAS-D.pdf`, opcional para validação do RBT12, anexo e DAS.

Os arquivos XLS legados são lidos pelo mecanismo Calamine, pois alguns arquivos
gerados pelo Domínio não são aceitos pelo leitor XLS tradicional. O sistema
compara os CNPJs antes de consolidar as informações.

O portal projeta de 6 a 36 meses usando média móvel configurável de 3 a 24
meses. O crescimento pode ser um percentual anual fixo ou calculado
automaticamente pela média geométrica das variações dos últimos 3, 6 ou 12
meses. Competências repetidas são deduplicadas e arquivos de CNPJs diferentes
são bloqueados.

O relatório inteligente possui duas modalidades:

- motor analítico local, sem envio de dados para serviços externos;
- IA generativa opcional, acionada pelo usuário mediante chave e modelo
  autorizados. As variáveis `OPENAI_API_KEY` e `OPENAI_MODEL` podem ser usadas
  para configuração local.

No modo transacional, o botão **Baixar modelo Excel para preenchimento** gera
as abas Faturamento, Entradas, Parametros_SN e Instruções com validações e
exemplos. O mesmo arquivo preenchido pode ser importado diretamente pelo campo
de arquivo único; os três uploads separados continuam disponíveis.

O dashboard Excel e as imagens dos exemplos podem ser recriados com:

```powershell
python gerar_artefatos.py
```

Os arquivos são gravados na pasta `resultados`.

> Por privacidade, os arquivos fiscais colocados em `documentos` e os artefatos
> de `resultados` não são enviados ao repositório Git. Cada instalação deve
> utilizar seus próprios arquivos locais.

## Verificação automatizada

```powershell
python -m unittest discover -s tests -v
```

O cálculo mantém separados os créditos disponíveis, utilizados no período e o
saldo estimado. A apropriação efetiva depende da operação, do documento fiscal
e das demais condições legais.

> Este simulador oferece apoio gerencial. As regras e alíquotas devem ser
> validadas conforme o anexo, a atividade e a etapa de transição vigente.
