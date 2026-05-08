# ValueBot — CLAUDE.md

## O que é este projeto

Bot de value betting com alertas via Telegram. Identifica apostas com Expected Value (EV) positivo
comparando odds de casas brasileiras com fair odds calculadas a partir de sharp books e modelos matemáticos.

**Foco principal:** Tênis ATP Challenger 250/500 (saibro sul-americano) e Tênis de Mesa WTT.
**Foco secundário:** Handebol EHF, MLB mid-week, Badminton BWF sub-Super 500.

---

## Stack

- **Backend:** Python 3.13 + FastAPI
- **Database:** PostgreSQL 16 (Railway em prod, Docker local)
- **Scheduler:** APScheduler 3.x (dentro do FastAPI)
- **Alertas:** Telegram Bot API (python-telegram-bot)
- **LLM contexto:** Claude Sonnet via Anthropic API
- **Deploy:** Railway
- **Testes:** pytest

---

## APIs externas

| API | Uso | Env var |
|-----|-----|---------|
| The Odds API v4 | Odds multi-casa principal | `ODDS_API_KEY` |
| API-Tennis | Rankings, H2H, forma ATP/ITF | `TENNIS_API_KEY` |
| API-Sports | Stats tênis de mesa, handebol | `SPORTS_API_KEY` |
| OpticOdds | Comparação Betano/Betsson/Pinnacle | `OPTIC_API_KEY` |
| Anthropic | Geração de contexto dos alertas | `ANTHROPIC_API_KEY` |
| Telegram | Envio de alertas | `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` |

---

## Regras de negócio — NUNCA violar

### Filtros de entrada (uma aposta só passa se TODOS forem verdadeiros)

1. **EV mínimo:** +4% (abaixo disso ignorar)
2. **Odd mínima:** 1.60 (evitar favoritos excessivos)
3. **Odd máxima:** 4.00 (evitar longshots)
4. **Liquidez Betfair mínima:** mercado deve ter volume real (> £500)
5. **Sem line movement contrário:** odd não pode ter subido nos últimos 30min
6. **Margem máxima da casa:** 10% (acima disso o mercado é ineficiente demais para confiar)

### Kelly Criterion

- Usar SEMPRE ¼ Kelly (Kelly fracionado conservador)
- Stake máximo por aposta: 3% da banca
- Máximo de 2 apostas simultâneas no mesmo esporte

### Fórmulas obrigatórias

```python
# Overround
overround = sum(1/odd for odd in odds) - 1

# Probabilidade implícita sem margem
prob_fair = (1/odd) / sum(1/odd for odd in odds)

# Expected Value
ev = (prob_real * lucro) - ((1 - prob_real) * stake)

# Kelly ¼
kelly = ((prob_real * odd - 1) / (odd - 1)) * 0.25
```

---

## Esportes e sports_keys (The Odds API)

```
tennis_atp      → ATP (todos os torneios)
tennis_wta      → WTA
table_tennis    → Tênis de Mesa
handball        → Handebol
volleyball      → Vôlei
baseball_mlb    → MLB
```

---

## Estrutura do projeto

```
valuebot/
├── CLAUDE.md              ← este arquivo (ler SEMPRE antes de qualquer ação)
├── .env                   ← API keys (NUNCA commitar)
├── .env.example           ← template sem valores reais
├── docker-compose.yml     ← PostgreSQL local
├── requirements.txt
│
├── app/
│   ├── main.py            ← FastAPI + lifespan + APScheduler
│   ├── config.py          ← settings via pydantic-settings
│   ├── scheduler.py       ← jobs agendados
│   │
│   ├── collectors/
│   │   ├── odds_collector.py     ← The Odds API + OpticOdds
│   │   ├── tennis_collector.py   ← API-Tennis (rankings, H2H)
│   │   └── sports_collector.py   ← API-Sports (tênis mesa, handebol)
│   │
│   ├── engine/
│   │   ├── calculator.py   ← overround, EV, fair odds
│   │   ├── elo.py          ← modelo Elo por superfície
│   │   ├── kelly.py        ← Kelly ¼ + sizing
│   │   └── filter.py       ← filtros de qualidade
│   │
│   ├── models/
│   │   ├── event.py        ← SQLAlchemy + Pydantic schemas
│   │   ├── odds.py
│   │   └── alert.py
│   │
│   ├── alerts/
│   │   ├── telegram.py     ← envio de mensagens
│   │   └── formatter.py    ← formata alerta com emojis e EV
│   │
│   └── database/
│       ├── connection.py   ← engine + session
│       └── migrations/     ← Alembic
│
└── tests/
    ├── test_calculator.py
    ├── test_elo.py
    ├── test_kelly.py
    └── test_filter.py
```

---

## Fluxo de execução

```
APScheduler (a cada 5min)
        ↓
odds_collector.py → busca eventos + odds de múltiplas casas
        ↓
tennis_collector.py → busca ranking, H2H, forma dos jogadores
        ↓
calculator.py → calcula overround + fair odds + EV
        ↓
elo.py → ajusta probabilidade com modelo Elo por superfície
        ↓
filter.py → aplica todos os filtros de qualidade
        ↓
kelly.py → calcula stake sugerido
        ↓
[se EV > 4%] detector de steam (compara com coleta anterior)
        ↓
formatter.py → monta mensagem do alerta
        ↓
[opcional] Sonnet → adiciona contexto qualitativo
        ↓
telegram.py → envia alerta
        ↓
database → persiste evento + odds + alerta
```

---

## Formato do alerta Telegram

```
🔴 ALERTA VALUE BET

🎾 Tênis ATP — Challenger Santos
⚔️  Dellien vs Hardt
🏠 Superfície: Saibro

📊 ANÁLISE
• Odd Betano: 1.88
• Fair odd: 1.28
• EV: +46.6%
• Prob. real: 78%
• Overround: 5.8%

💰 SIZING
• Banca: R$50,00
• Kelly ¼: 13.2%
• Stake sugerido: R$6,60

📈 LINE MOVEMENT
• Abertura: 1.90 (2h atrás)
• Atual: 1.88 (caindo ✅)

🕐 Início: 12:00 BRT
⚠️  Validade: até 11:50 BRT
```

---

## Regras para Claude Code

1. **Ler este arquivo COMPLETO antes de qualquer ação**
2. **TDD obrigatório** — escrever teste antes da implementação
3. **Nunca modificar fórmulas** de EV, Kelly e overround sem atualizar os testes
4. **Nunca hardcodar API keys** — sempre usar variáveis de ambiente via `config.py`
5. **Nunca commitar `.env`** — apenas `.env.example`
6. **Logging obrigatório** em todos os collectors e no engine
7. **Type hints obrigatórios** em todas as funções
8. **Documentar falhas** — se uma API retornar erro inesperado, registrar no log e continuar sem quebrar o scheduler
9. **Nunca apostas automáticas** — o bot APENAS alerta, decisão final é sempre do usuário
10. **Variância esperada** — um alerta com EV+10% pode perder. Isso é normal. O bot é avaliado por ROI ao longo de 100+ apostas, não por aposta individual

---

## Variáveis de ambiente (.env.example)

```env
# Database
DATABASE_URL=postgresql://valuebot:senha@localhost:5432/valuebot

# APIs
ODDS_API_KEY=
TENNIS_API_KEY=
SPORTS_API_KEY=
OPTIC_API_KEY=
ANTHROPIC_API_KEY=

# Telegram
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=

# Bot config
MIN_EV=0.04
MIN_ODD=1.60
MAX_ODD=4.00
MAX_MARGIN=0.10
KELLY_FRACTION=0.25
MAX_STAKE_PCT=0.03
POLL_INTERVAL_SECONDS=300
```

---

## Glossário rápido

- **EV (Expected Value):** retorno esperado de uma aposta. Positivo = lucrativo a longo prazo
- **Overround:** margem da casa embutida nas odds (também chamado de vig ou juice)
- **Fair odds:** odds sem margem da casa, representa a probabilidade real implícita
- **Kelly ¼:** fração do bankroll a apostar, calculada com critério de Kelly dividido por 4 para reduzir variância
- **Steam move:** queda brusca de odd em múltiplas casas simultaneamente — sinal de dinheiro profissional
- **Line movement:** variação da odd desde a abertura do mercado
- **Sharp book:** casa que precifica com baixa margem e aceita apostadores profissionais (ex: Pinnacle, Betfair)
- **Soft book:** casa com odds piores que sharps mas onde é possível apostar com limites maiores (ex: Betano, Betsson)
