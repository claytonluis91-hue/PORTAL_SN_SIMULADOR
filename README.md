# Grupo Nascel — Diagnóstico da Reforma Tributária (IBS/CBS)

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
- um único XLS/XLSX com várias competências separadas em pares de abas
  Resumido/Detalhado (por exemplo, `Resumido 01-2027` e `Detalhado 01-2027`);
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

## Simples Nacional conforme a LC 214/2025

No modo consolidado, o portal consulta a tabela vigente em 2027 e 2028 para os
Anexos I a V e identifica faixa, alíquota nominal, parcela a deduzir e partilha.
Na comparação com a mesma base, o DAS Por Dentro de 2027 preserva a alíquota
efetiva importada de 2026: a reforma altera a repartição interna, sem criar uma
diferença artificial de carga. Os valores são abertos no formato do PGDAS:
IRPJ, CSLL, CPP, IPI, ICMS, ISS, PIS/Pasep, Cofins, CBS e IBS.

- **Por Dentro:** CBS e IBS permanecem na guia única do Simples;
- **Por Fora:** os demais tributos formam o DAS residual e CBS/IBS são exibidos
  separadamente pelo regime regular, usando os valores calculados no relatório
  de Simulação da Reforma do Domínio.

No Anexo II, o portal mantém o IPI na partilha oficial de 2027/2028. Não há
zeramento nem troca automática de Anexo: eventual tratamento diferente somente
deve ser aplicado quando existir norma específica e verificável.

O Anexo e o RBT12 são preenchidos pelo PGDAS quando o CNPJ é compatível. Sem um
PGDAS compatível, o sistema sugere premissas com base nos relatórios e exige que
o usuário as revise. A planilha executiva exportada inclui a aba
`Simples_LC214` com a memória tributo por tributo.

O relatório inteligente possui duas modalidades:

- motor analítico local, sem envio de dados para serviços externos;
- Gemini opcional, acionado pelo usuário mediante chave configurada nos Secrets
  do Streamlit. Use `GOOGLE_API_KEY` (preferencial) ou `GEMINI_API_KEY` e,
  opcionalmente, `GEMINI_MODEL`. O portal possui um teste de conexão que não
  envia dados fiscais e atualiza a lista com os modelos realmente liberados
  para a credencial.

Exemplo de `.streamlit/secrets.toml`:

```toml
GOOGLE_API_KEY = "sua-chave"
GEMINI_MODEL = "gemini-3.5-flash"
```

Se a integração retornar erro de permissão, revise a chave no Google AI Studio.
Desde 19/06/2026, chaves padrão irrestritas podem ser recusadas pela Gemini API.
Erros de credencial, modelo indisponível, cota e indisponibilidade temporária são
traduzidos pelo portal em orientações objetivas, sem expor a chave ou a mensagem
técnica completa.

No modo transacional, o botão **Baixar modelo Excel para preenchimento** gera
as abas Faturamento, Entradas, Parametros_SN e Instruções com validações e
exemplos. O mesmo arquivo preenchido pode ser importado diretamente pelo campo
de arquivo único; os três uploads separados continuam disponíveis.

O portal e os relatórios seguem a identidade pública da Nascel Contabilidade:
Montserrat, azul-marinho `#16163F`, dourado `#F9AF44`, branco e fundo creme
`#F8F3EC`, com linguagem consultiva, direta e orientada à decisão.

O dashboard Excel inclui:

- `Como_Ler`: origem, fórmula ou premissa e interpretação de cada indicador;
- `Diagnostico_Nascel`: índice de confiança dos dados e pendências antes da decisão;
- `Decisao_Tributaria`: matriz comparativa de CBS/IBS dentro e fora do DAS;
- `Cronograma_Legal`: transição, impacto no Simples e ações recomendadas;
- `Fontes_Oficiais`: links para os textos legais compilados e orientações da RFB;
- `Pontos_Atencao`: significado de cada constatação e dados que a sustentam.

As orientações gerenciais consideram a LC 123/2006 e a LC 214/2025, com alerta
expresso para alterações posteriores vigentes, inclusive a LC 227/2026. O
dashboard e as imagens dos exemplos podem ser recriados com:

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
