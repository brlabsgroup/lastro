# Fase 0: Regras do jogo

Oct 13, 2026 · @Caue

## A tese, em prosa

Cripto tem uma propriedade que se mede com dado público: o que subiu nas últimas semanas tende a continuar subindo por mais um tempo, e o que caiu tende a continuar caindo. Não sempre, mas mais vezes do que o acaso, e por um motivo que não desaparece: as pessoas entram atrasadas na alta e saem atrasadas na queda, e o dinheiro grande se move devagar. Esse fenômeno tem nome, tendência e momentum, está documentado em décadas de literatura de finanças, e continua existindo porque quem o captura precisa aceitar longos períodos de lado e devolver parte de cada onda. O Lastro surfa esse fenômeno: compra o que vem subindo, vende para dólar o que vem caindo, e nunca tenta adivinhar o topo ou o fundo. Perde o começo e o fim de toda onda por desenho. Esse é o preço de não precisar prever.

O segundo pilar é que a volatilidade de cripto é bem mais previsível que o retorno. Se uma moeda oscilou muito esta semana, provavelmente vai oscilar muito na próxima; a direção, ninguém sabe, mas a agitação persiste. Isso permite dar a cada moeda uma fatia inversamente proporcional à sua agitação, para que nenhuma mande sozinha no resultado, e escalar a conta inteira para uma oscilação escolhida de antemão. Quando o mercado enlouquece, parte do capital vai para caixa; quando acalma, volta. O risco da conta deixa de ser o que o mercado decidir naquele mês e passa a ser o que o operador decidiu antes. É esse dimensionamento, e não a escolha de moedas, que separa compor por anos de quebrar num ciclo.

O terceiro pilar é que o varejo tem um nicho real: as 20 a 50 moedas mais líquidas depois do topo são pequenas demais para fundos entrarem sem mover o preço e lentas demais para interessar a quem compete em latência. Uma conta de varejo rebalanceando uma vez por dia não move nada. O Lastro não compete com market makers nem com fundos; opera onde eles não estão.

O que o sistema entrega não é uma estratégia secreta: tendência e momentum são públicos. O que ele entrega é disciplina verificável. Universo que inclui as moedas que morreram, para o teste não entrevistar só sobreviventes. Execução no candle seguinte, líquida de custo, para o backtest não fingir preços que não existiam. Hipóteses e critérios de morte escritos e carimbados antes de qualquer resultado, para ninguém, inclusive o autor, mover a trave depois de ver o placar. E uma tesouraria que define lucro como o que saiu para uma carteira própria, fora da exchange, e não como o número que a tela mostra. A aposta é que isso, feito com dado público e código aberto, vale mais do que qualquer promessa de retorno: quem roda o Lastro sabe exatamente o que está rodando, quanto custa, e o que teria acontecido se tivesse feito diferente.

O resultado esperado é modesto e explícito: 20 a 40% ao ano com quedas de 25 a 40% em algum momento, a maior parte do ganho concentrada em poucos meses de cada ciclo, e anos inteiros de lado. Se a pesquisa mostrar que nem isso se sustenta líquido de custo, o resultado é publicado do mesmo jeito. O laboratório é o produto; a estratégia é o que sobreviver a ele.

## 1. O que o sistema é

Lastro é um alocador spot long/caixa: uma vez por dia, decide quanto do capital de trading X fica em cada uma das 20 a 50 moedas mais líquidas da Binance e quanto fica em stablecoin, e manda só as ordens da diferença.

O objetivo é capturar boa parte das altas longas de cripto e pular boa parte das quedas, com a perda máxima decidida antes. Ele não prevê: reage ao que já está acontecendo e controla risco. Num ano típico: poucos meses ganhando muito, muitos meses de lado, alguns perdendo.

O que ele não é:

- Caçador de moeda nova (lançamentos, bonding curve, pump.fun).
- Bot intraday, market maker ou arbitrador.
- Alavancado ou vendido: só compra, ou fica em caixa.

As peças:

| Peça | O que faz | Regra que a governa |
| --- | --- | --- |
| Estratégia | Função pura: dados até t geram pesos-alvo | A mesma função roda no backtest e em produção |
| Executor | Lê saldos reais, calcula a diferença para o alvo e envia as ordens (modo automático) ou entrega a lista para o humano executar e confirmar (modo assistido) | Sem estado: reconcilia a cada rodada |
| Ledger | Registra cada fill em USDT e carrega a cotação da moeda fiscal do operador (BRL por default) naquele instante; o custo médio na moeda fiscal é derivado, nunca o registro primário | Bate centavo a centavo com o extrato da exchange |
| Backtester | Simula a estratégia no passado | Execução em t+1, custo modelado, benchmarks embutidos |
| Tesouraria | Gerencia X, varreduras e savings | Camada acima da estratégia, ligável no backtest |

&#91;embedded content: arquitetura · 7 peças e a tesouraria por cima\]

A estratégia recebe candles e devolve pesos-alvo; o executor reconcilia com a exchange e alimenta o ledger; o backtester consome os mesmos dados e a mesma função; a tesouraria define X por cima de tudo e varre o excedente para fora da exchange.

Produto: código aberto, licença permissiva, pessoa física rodando no próprio servidor. Sem custódia, sem sinal vendido.

## 2. Decisões fixadas

Estas decisões não se discutem de novo até o fim da Fase 3. Mudança só por nova versão deste documento, com justificativa escrita.

| Decisão | Escolha | Motivo |
| --- | --- | --- |
| Capital | Próprio, pessoa física, residência fiscal no Brasil | Define ledger, fisco e tamanho |
| Instrumento | Spot apenas (compra e vende a moeda em si, sem derivativo), em pares cotados em USDT: tudo é comprado e vendido contra USDT, e o caixa é USDT | Sem liquidação, sem funding, sem short |
| Exchange | Código agnóstico de plataforma: estratégia, backtester, tesouraria e ledger não conhecem exchange; só o conector (CCXT) e o modelo de custo são específicos. Venue inicial: Binance. Três modos de execução, setáveis: automático (ordens pela API), assistido (o bot gera a lista de ordens, o humano executa e confirma) e sinal (só o painel de pesos) | Pesquisa e execução no mesmo livro, arquivo histórico público, taxa de 0,10%. A migração da Binance Brasil em outubro de 2026 deixou a API para residentes incerta: o produto não pode depender de uma exchange. Trocar de venue recalibra custo, filtros e universo, não a estratégia |
| Horizonte | Swing em candle diário; 4h como variante testável | Compete em pesquisa, não em latência |
| Fora do escopo | Intraday (adiado até ter custo real medido), market making, arbitragem, moeda nova | Jogos de latência e PvP onde o varejo perde por estrutura |
| Distribuição | Código aberto sob Apache 2.0, self-hosted, sem custódia, sem venda de sinal | Elimina quase toda a questão regulatória |
| Dimensionamento | Vol targeting; exposição total nunca acima de 100% | Colhe a vol do mercado, controla a vol da conta |
| Lucro real | O que foi varrido para a carteira de savings | Tesouraria, seção 11 |
| Escala | Software agnóstico de X: dinheiro em Decimal, filtros da exchange, largura do universo derivada de X, capacidade como saída do backtester | Serve de centenas de dólares até onde o impacto permitir |
| Dados | Parquet e DuckDB para dado de mercado; SQLite para ledger, configuração e estado; fonte pública | Reproduzível por qualquer pessoa, custo zero, sem servidor |
| Expectativa pré-registrada | Sharpe líquido acima de 1 a 25 a 30% de vol-alvo: 20 a 40% ao ano com drawdown de 25 a 40% | O teste de teto (seção 8) diz se isso é conservador ou otimista |

## 3. Universo e largura

O universo é definido por regra e por data, nunca por lista fixa. Em cada data de decisão, a elegibilidade usa só dados disponíveis até aquela data.

Elegibilidade:

- Par spot cotado em USDT na Binance, com status de negociação ativo naquela data.
- Histórico mínimo de 90 candles diários.
- Volume mediano de 30 dias acima de US$ 5 milhões por dia.
- Excluídos: stablecoins e tokens atrelados a fiat ou ouro (USDC, FDUSD, TUSD, DAI, PAXG), tokens alavancados (UP, DOWN, BULL, BEAR), versões wrapped ou derivadas de outro ativo do universo (WBTC, WBETH, stETH).
- BNB entra no universo. Se o desconto de taxa estiver ligado, a reserva de BNB para taxas fica fora dos pesos.
- Ranking: mediana do volume em dólar dos últimos 30 dias. Os 50 primeiros elegíveis formam o universo máximo.

Largura N (quantas moedas o painel usa) é derivada de X, não fixada:

- Posição mínima útil = 5 vezes o notional mínimo do par, lido dos filtros da exchange (hoje 5 a 10 USDT na maioria dos pares). Com 10 USDT, a posição mínima é 50 USDT: um ajuste de 25% dela ainda passa do mínimo.
- N = floor(X / posição mínima), limitado entre N\_min e 50. N\_min é setável, default 5; o valor 1 é permitido e a H2 com K igual a 1 testa esse modo, mas abaixo de 5 moedas o relatório avisa que não há diversificação e que o giro de troca de líder pesa mais. Exemplos com mínimo de 10 USDT: X de 250 dá 5 moedas; 1.000 dá 20; 2.500 ou mais dá 50.
- Piso: abaixo de N\_min posições mínimas (250 USDT com notional de 10 e N\_min igual a 5), o bot se recusa a rodar e explica por quê.
- Posição que cai muito não fica presa: a saída por piso (seção 5) vende tudo antes de o valor ficar abaixo do mínimo da exchange.

Deslistagem:

- Anúncio de deslistagem: venda para caixa no próximo rebalanceamento, ou imediata se a data de saída for anterior.
- No backtest, o ativo sai do universo na data do anúncio. A posição é liquidada ao fechamento do dia seguinte com haircut de 2%.
- Suspensão sem aviso: posição marcada ao último preço e sinalizada como ilíquida. O backtester reporta o PnL com e sem ela.

## 4. Horizonte e execução

A decisão é tomada no fechamento do candle diário (00:00 UTC) e executada no candle seguinte. Nunca ao preço de fechamento da própria decisão: esse é o bug clássico de backtest, e aqui é proibido por desenho. São três relógios: decisão a cada candle, execução dentro da janela, risco contínuo (seção 12).

- Decisão: fechamento de t, com dados até t.
- Execução: janela aleatória dentro dos primeiros 60 minutos após a abertura de t+1. O deslocamento é sorteado por instalação, por dia, para que N cópias do bot não mandem as mesmas ordens no mesmo minuto. Dentro da janela, o minuto é escolhido pelo fluxo comprador dos candles de 30 e 5 minutos: fluxo a favor da ordem acelerando, executa; desacelerando, espera, até o limite da janela.
- Ordens: limitada ao melhor preço do livro, reposicionada a cada 30 segundos. Após 10 minutos sem preenchimento total, o remanescente vai a mercado.
- Fatiamento de ordem (TWAP): ordem acima de 1% do volume diário do ativo é dividida ao longo da janela (TWAP). O que não couber no teto de participação (seção 6) vai para o dia seguinte.
- Backtest: preenchimento ao preço de abertura de t+1 mais o custo modelado da seção 6.
- Variante 4h: mesma lógica, decisão no fechamento do candle de 4h. Entra como hipótese separada, com o custo do giro maior contado. Variante com fatiamento de posição (tranching): o sinal é avaliado a cada 4h, mas só 1/6 da mudança de posição é executada por avaliação. A primeira fatia sai 4 horas depois de o sinal virar, a posição completa se forma em um dia, nenhuma decisão depende de um único horário de fechamento, e um cruzamento falso custa 1/6 de ida e volta. O backtester compara as três: diário, 4h e 4h fatiado.
- Dado atrasado: se o último candle disponível tiver mais de 2 períodos de idade, o bot não opera e alerta.

## 5. Dimensionamento

Cada moeda com sinal ligado recebe peso inversamente proporcional à sua volatilidade, e a conta inteira é escalada para uma vol-alvo. A exposição total nunca passa de 100%: quando a vol-alvo pede menos que isso, o resto fica em caixa.

- Estimativa de vol por ativo: EWMA dos retornos logarítmicos diários, meia-vida de 20 dias (faixa testável: 10 a 40).
- Pesos brutos: entre os ativos com sinal ligado, proporcionais a 1 / vol, normalizados para somar 1. Contribuição de risco parecida por ativo; correlação entra só na vol da carteira.
- Vol-alvo da conta: 25% ao ano (default; 30% testável). A vol da carteira é estimada com a covariância EWMA dos pesos brutos.
- Fator de escala limitado a 1: nunca alavanca. Fator abaixo de 1 manda a diferença para caixa.
- Teto por ativo: o maior entre 25% de X e 1/N, para que com poucas moedas o teto não trave a exposição (faixa testável do piso: 20 a 35%). O excesso cortado pelo teto vai para caixa, não é redistribuído.
- Banda de não-negociação: um ativo só é rebalanceado se o desvio entre peso-alvo e peso-atual passar de 25% do peso-alvo, com piso de 0,5 ponto percentual de X, ou se o sinal mudou de estado. Ordem abaixo do notional mínimo é acumulada para o dia seguinte. Saída por piso: se o valor de uma posição cair abaixo de 1,5 vezes o notional mínimo, o bot vende tudo no próximo rebalanceamento, independente do sinal, para nenhuma posição virar pó. O backtester simula essa regra com o X configurado.
- Caixa: USDT. Rendimento sobre a perna de caixa é assunto da Fase 5.

Em fórmula, com s\_i igual a 1 (ligado) ou 0 (desligado), sigma\_i a vol do ativo, sigma\* a vol-alvo e sigma\_p a vol da carteira de pesos brutos r:

```latex
r_i = \frac{s_i / \sigma_i}{\sum_j s_j / \sigma_j}, \qquad w_i = \min\left(w_{\max},\; \lambda\, r_i\right), \qquad \lambda = \min\left(1,\; \frac{\sigma^{*}}{\hat{\sigma}_p(r)}\right)
```

A fração em caixa é 1 menos a soma dos w\_i.

## 6. Modelo de custo

Toda simulação é líquida de taxa, spread e impacto, com teto de participação por ativo. A variante de estresse dobra tudo, e uma estratégia que não sobrevive a ela morre (seção 10).

| Componente | Default | Observação |
| --- | --- | --- |
| Taxa | 0,10% por lado (taker) | 0,075% com BNB só se a reserva de BNB estiver modelada; o default assume taker por conservadorismo |
| Meio spread | 0,03% nos 10 primeiros do ranking; 0,08% do 11º ao 50º | Estimado dos dados quando houver livro; senão, o default |
| Impacto | vol diária do ativo vezes raiz(Q / ADV), coeficiente 1,0 | Modelo de raiz quadrada. Q = valor da ordem em dólar; ADV = volume médio diário de 30 dias |
| Teto de participação | 1% do ADV por ativo por dia | Excedente adiado para o dia seguinte; o backtester registra o atraso e o custo dele |
| Estresse | 2 vezes taxa, spread e impacto | Rodada obrigatória para toda hipótese |
| Varredura | Taxa de rede do saque, por rede | Lote mínimo tal que a taxa fique abaixo de 5% do valor varrido |
| Capacidade | X em que o CAGR líquido cai 20% em relação ao de X pequeno | Saída obrigatória do backtester, por hipótese |

O modelo tem três camadas de fonte, por venue. Ao vivo: taxa, filtros e livro de ofertas lidos da API no momento da ordem; o custo esperado de cada ordem é calculado contra o livro real e gravado antes de enviar. Histórico: o backtest usa a tabela de taxas vigente em cada data e o spread e impacto estimados do próprio dado, porque livro histórico não existe gratuitamente. Calibração: o custo real de cada fill (preço de decisão versus preço executado) é gravado no ledger e, com 60 dias de dados, substitui os defaults do venue. Se o custo real passar de 2 vezes o modelado, o modelo está errado e a pesquisa volta para a Fase 2 com os números novos.

## 7. Como se mede

A régua é B2: BTC com filtro de tendência, líquido de custo. Estratégia que não bate B2 em Sharpe e em Calmar é só um jeito caro de ter o mesmo beta.

| Código | Benchmark | Papel |
| --- | --- | --- |
| B0 | Caixa em USDT, retorno zero | Piso |
| B1 | BTC comprado e mantido | Beta puro |
| B2 | 100% BTC quando o fechamento está acima da média de 200 dias; caixa quando abaixo; líquido de custo | O que tem que ser batido |
| B3 | Cesto equal-weight do top N point-in-time, rebalanceado mensalmente | O que o universo entregou |
| B4 | Acumulação em BTC: aporte mensal, compra reforçada quando o preço está abaixo da média de 200 dias, nunca vende | O que um investidor disciplinado faz sem bot |

Métricas obrigatórias, todas líquidas de custo, no período fora da amostra, reportadas no agregado, por ano e por regime (BTC acima ou abaixo da média de 200 dias). Com aportes ligados, retorno ponderado pelo tempo (TWR) e pelo dinheiro (TIR), com o mesmo aporte aplicado a todos os benchmarks:

- Retorno: CAGR, vol anualizada, Sharpe líquido, Sharpe deflacionado pelo número de tentativas do log (seção 8), Calmar (CAGR sobre drawdown máximo).
- Risco: drawdown máximo e sua duração, pior mês, concentração de PnL (maior ano sobre o total).
- Operação: giro anual em múltiplos de X, custo total anual em % de X, fração do tempo em caixa, exposição média, número de ordens por mês.
- Robustez: Sharpe com cada parâmetro deslocado em 30% para cada lado; Sharpe no estresse de 2 vezes o custo; capacidade (seção 6).
- Fisco: total vendido por mês e ganho realizado por mês, na moeda fiscal configurada (BRL por default; USD para quem não configurar nenhuma). Operação: custo fixo mensal declarado pelo operador (servidor, domínio, o que for) descontado do PnL líquido; o relatório mostra o lucro antes e depois dele e o X mínimo em que a operação se paga.
- Tesouraria: curva de patrimônio com e sem varredura, e o custo do composto perdido.

## 8. Protocolo de pesquisa

Os dados fora da amostra são tocados uma vez por hipótese. Tudo que foi tentado fica registrado e desconta o Sharpe.

- Amostra: dados spot da Binance desde agosto de 2017.
- Desenvolvimento: agosto de 2017 a dezembro de 2022. Cobre o bear de 2018, o crash de março de 2020, o bull de 2021 e o bear de 2022.
- Holdout: janeiro de 2023 até a data de congelamento do dataset. Trancado até o fim da Fase 3.
- Walk-forward dentro do desenvolvimento: treino expansivo com mínimo de 2 anos, teste de 1 ano, passo de 1 ano. Três janelas de teste: 2020, 2021 e 2022. Parâmetros escolhidos só com dado de treino.
- Holdout: uma rodada por hipótese sobrevivente, com os parâmetros fixados no desenvolvimento. Falhou, morreu. Nenhum ajuste depois de ver o holdout.
- Log de tentativas: toda execução do backtester grava hash da configuração, data e hora, janela, métricas e hash do dataset, em arquivo append-only. O número de tentativas por hipótese alimenta o Sharpe deflacionado.
- Disciplina de parâmetros: no máximo 3 parâmetros livres por hipótese, grade pequena definida antes (seção 9), escolha pelo centro do platô e nunca pelo pico.
- Teste de teto, antes das hipóteses: B2, B3 e uma tendência simples sem otimização rodam no universo point-in-time, líquidos de custo, e o resultado é publicado. O teto não é hipótese: é a régua que calibra a expectativa da seção 2 antes de qualquer tese ser testada.
- Dataset congelado: o conjunto usado na Fase 3 tem hash publicado. Atualização gera novo hash e reabre o log, nunca sobrescreve.

## 9. Hipóteses pré-registradas

Quatro hipóteses, nesta ordem. H1 e H2 são independentes; H3 e H4 são camadas sobre elas. Todas usam o dimensionamento da seção 5 e o custo da seção 6. Nenhuma outra tese entra na Fase 3 sem nova versão deste documento.

| Hipótese | Sinal | Parâmetros livres (grade) | Morre se |
| --- | --- | --- | --- |
| H1 Tendência por ativo | Ligado quando o fechamento está acima da média de L dias; desligado abaixo. Variante: retorno dos últimos 12 meses, excluindo o último, positivo | L em {50, 100, 150, 200} dias; candle de decisão em {1d, 4h, 1h} | Não bate B2 líquido no walk-forward |
| H2 Momentum relativo | Rank do universo pelo retorno de J meses, pulando a última semana; os K primeiros ficam ligados | J em {1, 3, 6}; K em {1, 5, 10, N/2} | Não bate B2 nem B3 líquidos no walk-forward |
| H3 Regime de BTC | Alts só ligadas com BTC acima da média de M dias; BTC abaixo manda tudo para caixa | M em {100, 200}; aplicada sobre H1 e sobre H2 | Não melhora o Calmar da base |
| H4 Confirmação por volume (secundária) | Sinal de H1 só vale com volume de 30 dias acima de V vezes a média de 180 dias. Variante no 4h: desequilíbrio do volume comprador (taker) | V em {1,0, 1,2}; aplicada sobre H1 | Não melhora o Sharpe líquido de H1 |

O que cada uma testa: H1 pergunta se a tendência de cada moeda paga depois do custo. H2 pergunta se escolher as melhores entre elas paga mais que segurar todas. H3 pergunta se o regime de BTC protege o cesto. H4 responde à pergunta sobre volume: se ele acrescenta algo ao preço neste horizonte.

## 10. Critérios de morte

Escritos antes de qualquer resultado. Basta um critério falhar para a hipótese morrer na etapa, e não existe "consertar e rodar de novo" na mesma etapa.

| Etapa | Morre se | Limite |
| --- | --- | --- |
| Backtest (walk-forward, líquido) | Sharpe líquido agregado abaixo de | 0,7 |
| Backtest | Sharpe deflacionado não distinguível de zero | p acima de 0,05 |
| Backtest | Não bate B2 em Sharpe e em Calmar | Ambos |
| Backtest | Drawdown máximo acima de | 45% com vol-alvo de 25% |
| Backtest | Concentração: maior ano acima de 70% do PnL, ou menos da metade dos anos positivos | Qualquer um |
| Backtest | Fragilidade: deslocar um parâmetro em 30% inverte o sinal do Sharpe | Qualquer parâmetro |
| Backtest | Estresse: Sharpe com custo dobrado abaixo de | 0,4 |
| Backtest | Giro anual acima de | 30 vezes X |
| Holdout | Qualquer critério acima, em uma única rodada | Sem ajuste |
| Shadow (60 dias) | Custo real acima de 2 vezes o modelado; drift entre alvo e real acima de 5 pontos sem causa identificada; mais de 2 falhas de pipeline | Volta para a Fase 2 ou 4 |
| Live | Drawdown acima de 1,5 vezes o pior do backtest | Para e investiga |
| Live | 6 meses com Sharpe realizado negativo enquanto B2 é positivo | Para |
| Live | Qualquer kill switch da seção 12 | Para até revisão manual |

Hipótese morta é publicada com o mesmo destaque de hipótese viva. O resultado esperado da Fase 3 é que a maioria morra.

## 11. Tesouraria

A tesouraria gerencia o capital de trading X e define lucro real como o que saiu para a carteira de savings. É uma camada separada da estratégia, simulada no backtester com liga e desliga, para que o custo do composto perdido apareça em número.

| Parâmetro | Default | Setável para |
| --- | --- | --- |
| X, capital de trading | Definido pelo usuário | Qualquer valor acima do piso da seção 3. Toda mudança é evento no ledger, com data |
| Aportes | Nenhum | Ao vivo, nunca automático: depósito que chega na conta aparece como aporte pendente e o operador confirma se entra em X ou fica fora. No backtester, valor fixo por mês, aplicado igual a todos os benchmarks |
| Gatilho de varredura | Marca d'água alta: só o que excede X | Calendário mensal |
| Momento da varredura | Quando a exposição está em 20% ou menos (bot em caixa) | Fim de mês |
| Lote mínimo | Taxa de rede abaixo de 5% do valor, nunca abaixo do mínimo de saque | Valor fixo |
| Mão | Única: sem reposição | Dupla: repõe até X quando abaixo, limitada a Z% do savings por ano |
| Moeda do savings | USDT | BTC ou outra do universo |
| Destino | Endereço fora da exchange, em allowlist de saque | Obrigatório fora da exchange |
| Subida de X | Desligada | Manual é o default: o operador muda X quando quiser e cada mudança vira evento no ledger com contrafactual. Por regra, opcional: X sobe Z% a cada N meses acima da marca d'água, para quem preferir tirar a mão da decisão |
| Execução da varredura | Bot notifica, humano saca | Chave de saque separada, só para o endereço em allowlist |

Relatório da tesouraria:

- Lucro realizado por moeda do savings, na unidade dela (USDT, BTC), e o valor atual em BRL e em USD.
- Patrimônio total marcado a mercado: trading mais savings.
- Contrafactual de cada mudança manual de X: quanto o sistema teria feito se X não tivesse mudado. É o timing do operador em número.
- Curva de patrimônio com e sem varredura, para ver o preço de não compor.

Troca de USDT por BTC no savings é permuta e entra no ledger como alienação, com custo de aquisição em BRL carregado até a venda futura.

## 12. Segurança operacional

O pior caso é perder X. Nenhuma falha de código, de exchange ou de operador pode perder mais que isso.

- Teto na exchange: fica X mais um buffer de 5% para taxas. O savings fica fora, em self-custody.
- Chave de API: só negociação, sem saque, restrita a IP fixo. Saque automatizado, se ligado, usa chave separada com destino em allowlist.
- Kill switches, todos com alerta no Telegram:
  - Perda diária acima de 5% de X: para.
  - Dado com mais de 2 candles de idade: não opera.
  - Ordem acima de 30% de X: rejeitada.
  - 3 erros consecutivos da API: para.
  - Drift entre carteira-alvo e real acima de 5 pontos percentuais: alerta e não abre posição nova.
- Relógio de risco, contínuo: posição que cai mais de 3 vezes a sua vol diária dentro do dia sai na hora, sem esperar o fechamento do candle de decisão. Só dispara em cauda; o backtester simula com candles de 1 hora.
- Executor sem estado: a cada rodada lê os saldos reais e reconcilia. Reiniciar não duplica ordem (idempotência por identificador de ordem do cliente).
- Manada de clones: janela aleatória e ordens limitadas, como na seção 4.
- Relatório diário por Telegram (gratuito; outros canais por plugin): estado atual, estado proposto, ordens executadas, estado final, além de slippage real versus modelado e saldo na exchange versus teto. No modo assistido, a lista de ordens vai por essa via e a confirmação volta por ela. Comparação de venues: em shadow, o mesmo painel de pesos é precificado contra o livro de cada venue conectado, e o relatório mostra o custo diário de execução em cada um; ordens reais só saem no venue ativo.
- Infra: VPS pequena, job agendado, logs e snapshot diário dos saldos guardados fora da VPS.
- Retomada: após qualquer parada, só volta com revisão manual e registro do motivo no log.

## 13. Dados e ledger

O dado é público, local e reproduzível. O ledger existe desde o primeiro fill e bate com o extrato da exchange.

Dados (requisitos da Fase 1):

- Fonte principal: o arquivo histórico público da Binance (data.binance.vision), candles spot de 1 dia, 4 horas e 1 hora, mais 30 e 5 minutos para o timing de execução (seção 4) e 1 hora para o relógio de risco (seção 12). A primeira tarefa da Fase 1 é confirmar que ele cobre pares já deslistados; se faltar algo, complementar pela API histórica e documentar.
- Metadados: snapshots dos filtros e do status de cada par (exchangeInfo), datas de listagem e de deslistagem.
- Série USDT/BRL: par USDTBRL da própria Binance, cruzado com a PTAX do Banco Central, para o custo de aquisição em real.
- Checagens antes de qualquer uso: gaps, duplicatas, candles com volume zero, timestamps em UTC, retornos acima de 50% num candle confrontados com fonte secundária.
- Hash do dataset publicado; Parquet particionado por par e ano para dado de mercado (candles, livros de ofertas), que é escrito uma vez e lido milhares; DuckDB para consulta. Tudo que é da operação (ledger, eventos de tesouraria, log de tentativas, configuração, estado do executor, regras fiscais) vive num banco SQLite: um arquivo, sem servidor, com transações, que o DuckDB lê direto para cruzar com os candles.

Ledger (desde o dia 1):

- Por fill: data e hora, par, lado, quantidade, preço, taxa e moeda da taxa, cotação USDT/BRL do momento, custo médio em BRL, ganho realizado em BRL.
- Por mês: total vendido em BRL, ganho ou prejuízo realizado, saldo de prejuízo a compensar.
- Permutas (USDT por BTC na tesouraria) registradas como alienação.
- Exportações: ganho de capital mensal no formato do GCAP e operações do mês no formato da DeCripto. Regras fiscais em tabela do banco, por país e com vigência (moeda, período de apuração, isenção, alíquotas, método de custo, se permuta conta como alienação): o Brasil entra na instalação, outro país é um seed novo, e regra que muda de data ganha linha nova sem apagar a antiga.
- Reconciliação mensal com o extrato da exchange; retenção de 5 anos.
- Eventos de tesouraria (mudança de X, varredura, reposição) no mesmo ledger. Regime fiscal setável por período, com data de corte: entidade estrangeira (apuração anual, Lei 14.754, DeCripto pelo usuário) até a migração, entidade brasileira (apuração mensal, GCAP, isenção de R$ 35 mil, reporte pela exchange) depois; o ledger exporta nos dois formatos e não compensa prejuízo entre os períodos. Permuta entre criptos conta como alienação para o limite mensal. Snapshot de saldos e custo médio por ativo na véspera da migração.

Nada disto é orientação contábil. As regras de isenção, apuração e compensação são confirmadas com contador antes da Fase 1 (seção 15).

## 14. Publicação e carimbo

Este documento é carimbado antes de qualquer resultado existir. Depois disso, hipóteses e critérios não mudam: mudam de versão.

- Carimbo: hash SHA-256 da versão 0.1 ancorado no Bitcoin via OpenTimestamps, publicado no repositório junto com o arquivo de prova. Custo zero, verificável por qualquer um.
- Imutabilidade: as seções 9 e 10 são congeladas pelo carimbo. Qualquer mudança vira versão nova, com justificativa, e o backtester reporta os resultados contra a versão carimbada em que a hipótese nasceu.
- Repositório público desde a Fase 1: licença Apache 2.0, aviso de que nada ali é recomendação de investimento, sem garantia de resultado.
- Log de tentativas público, com os hashes de configuração e de dataset.
- Em produção: hash dos pesos-alvo de cada dia publicado no próprio dia; o registro completo das decisões é revelado com 7 dias de atraso.
- Resultado negativo é publicado com o mesmo destaque de resultado positivo. "Testei 4 hipóteses, 3 morreram" é um entregável, não uma falha.
- O que não se publica: chaves, saldos, endereços de saque, e qualquer número de PnL sem o log que o sustenta.

## 15. Fases e perguntas abertas

As fases 1 a 3 não arriscam um centavo e são onde o projeto vive ou morre. Cada fase só fecha pelo seu critério de saída.

1. Fase 1, dados: pipeline reproduzível, dataset com hash, cobertura de deslistados confirmada, checagens passando. Sai quando o dataset se reproduz em outra máquina com o mesmo hash.
2. Fase 2, backtester e teto: execução em t+1, custo da seção 6, benchmarks da seção 7, walk-forward, log de tentativas, Sharpe deflacionado. Sai quando B1 e B2 reproduzem uma referência conhecida e o teste de teto está publicado.
3. Fase 3, pesquisa: H1 a H4 no walk-forward; sobreviventes no holdout, uma vez. Sai com zero ou mais hipóteses vivas e o relatório publicado, inclusive as mortas.
4. Fase 4, shadow por 60 dias: mesmo código, dado real, nenhuma ordem enviada. Sai com custo real até 2 vezes o modelado e 30 dias seguidos sem falha de pipeline.
5. Fase 5, live pequeno: X inicial, kill switches ligados, tesouraria ligada, rendimento na perna de caixa avaliado. Revisão mensal contra a seção 10.

Perguntas abertas antes da Fase 1:

- [ ] Contador: confirmar o regime da entidade brasileira (ganho de capital mensal, isenção de R$ 35 mil sobre o total alienado no mês, 15% a 22,5%, DARF, compensação de prejuízo) e os cortes: operações até a migração ficam na Lei 14.754 (anual, 15%, sem isenção) e não compensam com as posteriores. O projeto mantém os dois regimes por configuração (seção 13); o default passa a ser o brasileiro.
- [ ] Binance: a migração para as entidades brasileiras (Binance Brasil CCVM, ex-Sim;paul, para reais; BBrasil PSAV para cripto) é automática para residentes no Brasil, prazo 29 de outubro de 2026; 8 produtos restritos e 22 tokens deslistados em 27 de outubro; saques e depósitos internacionais exigem finalidade e contraparte a partir de 1º de novembro (Res. BCB 521). Segue aberto: o spot continua no livro global de USDT? Chaves de API continuam para residentes? Verificar no FAQ oficial e, depois da migração, por teste de API na própria conta. Se a API cair, a Fase 5 roda em modo assistido ou sinal, ou em outro venue; as Fases 1 a 3 não mudam.
- [x] O arquivo histórico da Binance cobre pares deslistados: confirmado em 14 de outubro de 2026. BTCBUSD-1d-2023-06.zip responde 200, última modificação em 4 de julho de 2023, depois da deslistagem dos pares de BUSD. Na Fase 1, a lista de pares que já existiram sai da enumeração das pastas do arquivo, e o mês de deslistagem é o último arquivo presente em cada par.
- [x] Vol-alvo: configurável pelo operador; default 25%; o backtester reporta a 25% e a 30%.
- [x] BNB: taxa cheia até a Fase 5; desconto e reserva entram depois, se o giro medido justificar.
- [x] Variante 4h: resolvida. Entrou na grade da H1 (1d, 4h, 1h) e como fatiamento de posição na seção 4.
- [ ] Nome: Lastro, escolhido em 14 de outubro de 2026; repositório brlabsgroup/lastro e nome no PyPI disponíveis. Licença: Apache 2.0, decidida em 14 de outubro de 2026.

Fontes consultadas em 13 de outubro de 2026:

- [Arquivo público da Binance, cliente binance-vision](https://pypi.org/project/binance-vision/)
- [Taxas spot da Binance e registro na CNV](https://www.datawallet.com/de/krypto/beste-krypto-b%C3%B6rsen-argentinien)
- [Taxas da Coinbase Advanced](https://coincodecap.com/coinbase-fees)
- [DeCripto, IN RFB 2.291/2025](https://guiatributario.net/2025/11/17/)
- [IN 1888 revogada a partir de 1º de julho de 2026](https://www.legisweb.com.br/legislacao/?id=377332)
- [Isenção de R$ 35 mil e exchange estrangeira no IR 2026](https://www.adrianofreire.com.br/blog/como-declarar-criptomoedas-ir-2026)
- [Resoluções BCB 519, 520 e 521](https://renovainvest.com.br/?p=73161)
