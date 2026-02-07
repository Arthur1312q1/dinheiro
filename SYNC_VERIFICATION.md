# VERIFICAÇÃO DE SINCRONIZAÇÃO

Este documento descreve o procedimento para validar que o bot está executando EXATAMENTE as mesmas trades do TradingView.

## PRÉ-REQUISITOS

1. **Bot Python** com as modificações implementadas
2. **TradingView** com a estratégia "Adaptive Zero Lag EMA v2"
3. **Mesmos parâmetros** em ambos os sistemas:
   - Period: 20
   - Adaptive Method: Cos IFM
   - Gain Limit: 900
   - Threshold: 0.0
   - SL Points: 2000
   - TP Points: 55
   - Risk: 0.01
   - Max Lots: 100

## PROCEDIMENTO DE VALIDAÇÃO

### Passo 1: Preparar TradingView

1. Abra o Pine Script no TradingView
2. Configure os mesmos parâmetros listados acima
3. Habilite a visualização de:
   - `buy_signal` e `sell_signal`
   - `pendingBuy` e `pendingSell`
   - `strategy.position_size`
   - `balance` (strategy.initial_capital + strategy.netprofit)

### Passo 2: Exportar Dados do TradingView

1. Use o script de exportação (separado) para exportar:
   - Timestamp de cada barra
   - Preço Open, High, Low, Close
   - Sinais `buy_signal` e `sell_signal`
   - `pendingBuy` e `pendingSell`
   - `position_size`
   - `balance`

2. Salve como CSV: `tv_export_YYYY-MM-DD.csv`

### Passo 3: Executar Bot em Modo Validação

1. Inicie o bot com sincronização NTP ativada:
   ```bash
   python main.py --validate --sync-ntp
