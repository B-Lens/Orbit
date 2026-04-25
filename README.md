# Orbit 🪐

## Overview

Orbit is an AI-based trading framework that bridges research experimentation and production trading. It integrates classical strategies, machine learning models, and reinforcement learning experiments into a modular, automation-friendly architecture for both market intelligence and trading operations.

### Key Features
- **Market Intelligence:** Harness Reddit and news sentiment data for actionable insights using LLM-powered analysis.
- **Automated Trading:** Execute and monitor Binance Futures trades with precision, including SL/TP lifecycle management.
- **Modular Design:** Easily integrate custom strategies via a lazy-loading strategy registry.
- **Research to Production:** Smooth transition from trading ideas to live trading with contradict simulation support.

## Setup and Installation

### Prerequisites
- Python 3.10+
- Linux environment (recommended)
- Redis and MongoDB (required for trade state and OHLCV data)

### Installation Steps
1. Clone the repository:
   ```
   git clone https://github.com/ipankaj/Orbit.git
   ```
2. Navigate into the project directory:
   ```
   cd Orbit
   ```
3. Install dependencies using Poetry:
   ```
   poetry install
   ```

## Usage

### Running the Project
Start the application with:
```
poetry run orbit
```
This command launches `BinanceAutomation`, the top-level trading automation controller found in `src/orbit/core/main.py`. It orchestrates three long-running daemon threads:

1. **Signal Analysis** — aligns to 15-minute candle boundaries, generates and processes trading signals via `SignalAnalyzer`, and sleeps for 900 seconds (15 minutes) between cycles.
2. **Trade Checker** — monitors active Binance Futures positions and manages SL/TP lifecycle via `TradeChecker`.
3. **Sentiment Cron** — runs hourly sentiment analysis via `Croner` and `SentimentWorkflow`.

A fourth daemon thread, **MonitorThread**, periodically checks all worker threads every 300 seconds and sends Discord alerts if any have stopped.

Configuration is loaded from `config/config.json` via `config/config.py`. Key configuration fields include:

- `trading_pairs` — symbols analysed for signals: `BTCUSDT`, `ETHUSDT`, `BCHUSDT`.
- `trade_checker_pair` — symbols monitored for open positions: `BCHUSDT`, `BTCUSDT`, `BNBUSDT`, `ETHUSDT`, `MKRUSDT`, `LTCUSDT`, `SOLUSDT`, `ATOMUSDT`, `XRPUSDT`.
- `risk_management` — per-symbol risk fractions (`BTCUSDT`: 0.01, `ETHUSDT`: 0.03, `BCHUSDT`: 0.03) and a shared `stop_loss_percent` of 1.
- `FUTURE_LEVERAGE` — default futures leverage of `2` (overridden to `5` for `BTCUSDT` in code).
- `FIXED_TRADE_AMOUNT` — fixed notional per symbol (`BTCUSDT`: $600, `ETHUSDT`: $30, `BCHUSDT`: $30).
- `cooldown_hours` — per-symbol cooldown after a trade (e.g. `SOLUSDT`: 8 h, `ETHUSDT`: 1 h, others: 2 h).
- `trading_pairs_precision` — quantity precision (decimal places) per symbol used when placing orders.

## Contributing

We welcome contributions that improve:

- **Stability**
- **Observability**
- **Performance**
- **Innovation in Research Methods**
- **Documentation**

**How to Contribute:**
- Create a new branch for your feature or bug fix.
- Make your changes and ensure tests pass.
- Submit a pull request with a clear description of your changes.

For any questions or to discuss ideas, please create an issue.

## System Architecture

### High-Level Runtime Flow

<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Orbit — System Architecture</title>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet"/>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #020617;
      color: #e2e8f0;
      font-family: 'JetBrains Mono', monospace;
      min-height: 100vh;
      padding: 32px 24px 48px;
    }

    /* ── Header ── */
    .header {
      display: flex;
      align-items: center;
      gap: 14px;
      margin-bottom: 8px;
    }
    .pulse-dot {
      width: 12px; height: 12px;
      border-radius: 50%;
      background: #22d3ee;
      box-shadow: 0 0 8px #22d3ee;
      animation: pulse 2s ease-in-out infinite;
      flex-shrink: 0;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50%       { opacity: 0.5; transform: scale(0.85); }
    }
    .header h1 {
      font-size: 22px; font-weight: 700;
      background: linear-gradient(135deg, #22d3ee, #a78bfa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .subtitle {
      font-size: 11px; color: #64748b;
      margin-bottom: 28px;
      margin-left: 26px;
      letter-spacing: 0.06em;
    }

    /* ── Diagram card ── */
    .diagram-card {
      background: #0b1120;
      border: 1px solid #1e293b;
      border-radius: 14px;
      padding: 20px;
      overflow-x: auto;
    }

    /* ── Summary cards ── */
    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 16px;
      margin-top: 24px;
    }
    .card {
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 10px;
      padding: 18px 20px;
    }
    .card-header {
      display: flex; align-items: center; gap: 10px;
      margin-bottom: 12px;
    }
    .card-dot {
      width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
    }
    .cyan   { background: #22d3ee; }
    .emerald{ background: #34d399; }
    .violet { background: #a78bfa; }
    .amber  { background: #fbbf24; }
    .card-header h3 {
      font-size: 11px; font-weight: 600;
      color: #e2e8f0; letter-spacing: 0.04em; text-transform: uppercase;
    }
    .card ul { list-style: none; }
    .card ul li {
      font-size: 10px; color: #94a3b8;
      padding: 3px 0; border-bottom: 1px solid #1e293b;
    }
    .card ul li:last-child { border-bottom: none; }

    /* ── Footer ── */
    footer {
      margin-top: 28px;
      font-size: 9px; color: #334155;
      letter-spacing: 0.08em;
      text-align: center;
    }
  </style>
</head>
<body>

  <div class="header">
    <div class="pulse-dot"></div>
    <h1>Orbit 🪐 — System Architecture</h1>
  </div>
  <p class="subtitle">AI-POWERED AUTOMATED TRADING FRAMEWORK · BINANCE FUTURES · PYTHON 3.10+</p>

  <div class="diagram-card">
    <svg viewBox="0 0 1060 740" xmlns="http://www.w3.org/2000/svg" style="width:100%;min-width:860px;display:block;">
      <defs>
        <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
          <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/>
        </pattern>
        <marker id="arr" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0,10 3.5,0 7" fill="#64748b"/>
        </marker>
        <marker id="arr-cyan" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0,10 3.5,0 7" fill="#22d3ee"/>
        </marker>
        <marker id="arr-emerald" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0,10 3.5,0 7" fill="#34d399"/>
        </marker>
        <marker id="arr-violet" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0,10 3.5,0 7" fill="#a78bfa"/>
        </marker>
        <marker id="arr-rose" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0,10 3.5,0 7" fill="#fb7185"/>
        </marker>
        <marker id="arr-amber" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0,10 3.5,0 7" fill="#fbbf24"/>
        </marker>
        <marker id="arr-orange" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0,10 3.5,0 7" fill="#fb923c"/>
        </marker>
      </defs>

      <!-- Background -->
      <rect width="1060" height="740" fill="#0b1120"/>
      <rect width="1060" height="740" fill="url(#grid)"/>

      <!-- ══════════════════════════════════════════════════════ -->
      <!-- ARROWS (drawn first, behind all boxes)                -->
      <!-- ══════════════════════════════════════════════════════ -->

      <!-- External Data → Reddit/News Client -->
      <line x1="90" y1="110" x2="90" y2="148" stroke="#22d3ee" stroke-width="1.2" stroke-dasharray="4,3" marker-end="url(#arr-cyan)"/>

      <!-- Reddit/News → WeightedRedditAnalyzer / LLM -->
      <line x1="140" y1="188" x2="230" y2="188" stroke="#22d3ee" stroke-width="1.2" marker-end="url(#arr-cyan)"/>

      <!-- MarketIndicators → SentimentWorkflow -->
      <line x1="90" y1="258" x2="188" y2="258" stroke="#22d3ee" stroke-width="1.2" stroke-dasharray="4,3" marker-end="url(#arr-cyan)"/>

      <!-- WeightedRedditAnalyzer → SentimentWorkflow -->
      <line x1="310" y1="198" x2="310" y2="238" stroke="#22d3ee" stroke-width="1.2" marker-end="url(#arr-cyan)"/>

      <!-- SentimentWorkflow → Sentiment Score / Signal -->
      <line x1="360" y1="258" x2="435" y2="258" stroke="#22d3ee" stroke-width="1.2" marker-end="url(#arr-cyan)"/>

      <!-- Sentiment Score → Redis sentiment cache -->
      <line x1="530" y1="248" x2="530" y2="195" stroke="#a78bfa" stroke-width="1.2" marker-end="url(#arr-violet)"/>

      <!-- Sentiment Score → MongoDB crypto_sentiment -->
      <line x1="570" y1="258" x2="640" y2="258" stroke="#a78bfa" stroke-width="1.2" marker-end="url(#arr-violet)"/>

      <!-- Sentiment Signal → SignalAnalyzer (cross-zone) -->
      <line x1="530" y1="268" x2="530" y2="370" stroke="#22d3ee" stroke-width="1.2" stroke-dasharray="5,3" marker-end="url(#arr-cyan)"/>

      <!-- MongoDB OHLCV → SignalAnalyzer -->
      <line x1="870" y1="420" x2="760" y2="420" stroke="#a78bfa" stroke-width="1.2" marker-end="url(#arr-violet)"/>

      <!-- SignalAnalyzer → OrderManager -->
      <line x1="650" y1="420" x2="700" y2="465" stroke="#34d399" stroke-width="1.4" marker-end="url(#arr-emerald)"/>
      <!-- label -->
      <text x="656" y="448" fill="#34d399" font-size="8" font-family="JetBrains Mono">signal</text>

      <!-- OrderManager → Binance Futures API -->
      <line x1="780" y1="500" x2="870" y2="500" stroke="#fbbf24" stroke-width="1.4" marker-end="url(#arr-amber)"/>

      <!-- OrderManager → Redis trade/order state -->
      <line x1="730" y1="525" x2="730" y2="595" stroke="#a78bfa" stroke-width="1.2" marker-end="url(#arr-violet)"/>

      <!-- OrderManager → ContradictSimulator -->
      <line x1="680" y1="505" x2="580" y2="540" stroke="#64748b" stroke-width="1.2" stroke-dasharray="4,3" marker-end="url(#arr)"/>

      <!-- OrderManager → TradeChecker -->
      <line x1="730" y1="525" x2="730" y2="555" stroke="#34d399" stroke-width="1.2" marker-end="url(#arr-emerald)"/>
      <line x1="680" y1="570" x2="610" y2="570" stroke="#34d399" stroke-width="1.2" marker-end="url(#arr-emerald)"/>

      <!-- TradeChecker ↔ Redis -->
      <line x1="540" y1="580" x2="540" y2="608" stroke="#a78bfa" stroke-width="1.2" marker-end="url(#arr-violet)"/>

      <!-- MonitorThread → all threads (dashed rose) -->
      <line x1="200" y1="445" x2="200" y2="575" stroke="#fb7185" stroke-width="1" stroke-dasharray="4,3"/>
      <line x1="200" y1="575" x2="490" y2="575" stroke="#fb7185" stroke-width="1" stroke-dasharray="4,3"/>
      <line x1="200" y1="445" x2="340" y2="415" stroke="#fb7185" stroke-width="1" stroke-dasharray="4,3"/>
      <line x1="200" y1="445" x2="280" y2="500" stroke="#fb7185" stroke-width="1" stroke-dasharray="4,3" marker-end="url(#arr-rose)"/>

      <!-- MonitorThread → Discord -->
      <line x1="250" y1="420" x2="300" y2="360" stroke="#fb7185" stroke-width="1" stroke-dasharray="4,3" marker-end="url(#arr-rose)"/>

      <!-- Config → BinanceAutomation -->
      <line x1="100" y1="385" x2="160" y2="400" stroke="#64748b" stroke-width="1.1" stroke-dasharray="3,3" marker-end="url(#arr)"/>

      <!-- ══════════════════════════════════════════════════════ -->
      <!-- ZONE: EXTERNAL DATA SOURCES                           -->
      <!-- ══════════════════════════════════════════════════════ -->
      <!-- boundary -->
      <rect x="18" y="42" width="730" height="310" rx="12" fill="none" stroke="#fbbf24" stroke-width="1" stroke-dasharray="8,4"/>
      <text x="30" y="37" fill="#fbbf24" font-size="9" font-family="JetBrains Mono" font-weight="600">MARKET INTELLIGENCE ENGINE</text>

      <!-- External Sources -->
      <rect x="30" y="60" width="120" height="52" rx="6" fill="#0f172a"/>
      <rect x="30" y="60" width="120" height="52" rx="6" fill="rgba(120,53,15,0.3)" stroke="#fbbf24" stroke-width="1.5"/>
      <text x="90" y="80" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">Reddit</text>
      <text x="90" y="94" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">News APIs</text>
      <text x="90" y="106" fill="#fbbf24" font-size="8" text-anchor="middle" font-family="JetBrains Mono">External</text>

      <!-- Reddit/News Client -->
      <rect x="30" y="148" width="120" height="52" rx="6" fill="#0f172a"/>
      <rect x="30" y="148" width="120" height="52" rx="6" fill="rgba(8,51,68,0.4)" stroke="#22d3ee" stroke-width="1.5"/>
      <text x="90" y="168" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">Reddit Client</text>
      <text x="90" y="182" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">News Client</text>
      <text x="90" y="194" fill="#22d3ee" font-size="7" text-anchor="middle" font-family="JetBrains Mono">data ingestion</text>

      <!-- MarketIndicators -->
      <rect x="30" y="228" width="160" height="52" rx="6" fill="#0f172a"/>
      <rect x="30" y="228" width="160" height="52" rx="6" fill="rgba(8,51,68,0.4)" stroke="#22d3ee" stroke-width="1.5"/>
      <text x="110" y="248" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">MarketIndicators</text>
      <text x="110" y="262" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">VIX / Fear &amp; Greed</text>
      <text x="110" y="274" fill="#22d3ee" font-size="7" text-anchor="middle" font-family="JetBrains Mono">market context</text>

      <!-- WeightedRedditAnalyzer / LLM -->
      <rect x="228" y="163" width="164" height="52" rx="6" fill="#0f172a"/>
      <rect x="228" y="163" width="164" height="52" rx="6" fill="rgba(6,78,59,0.4)" stroke="#34d399" stroke-width="1.5"/>
      <text x="310" y="182" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">WeightedReddit</text>
      <text x="310" y="196" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">Analyzer + LLM</text>
      <text x="310" y="208" fill="#34d399" font-size="7" text-anchor="middle" font-family="JetBrains Mono">NLP scoring</text>

      <!-- SentimentWorkflow -->
      <rect x="188" y="228" width="164" height="52" rx="6" fill="#0f172a"/>
      <rect x="188" y="228" width="164" height="52" rx="6" fill="rgba(6,78,59,0.4)" stroke="#34d399" stroke-width="1.5"/>
      <text x="270" y="248" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">SentimentWorkflow</text>
      <text x="270" y="262" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">Croner · hourly</text>
      <text x="270" y="274" fill="#34d399" font-size="7" text-anchor="middle" font-family="JetBrains Mono">aggregates + ranks</text>

      <!-- Sentiment Score & Signal -->
      <rect x="432" y="228" width="130" height="52" rx="6" fill="#0f172a"/>
      <rect x="432" y="228" width="130" height="52" rx="6" fill="rgba(6,78,59,0.4)" stroke="#34d399" stroke-width="1.5"/>
      <text x="497" y="248" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">Sentiment</text>
      <text x="497" y="262" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">Score &amp; Signal</text>
      <text x="497" y="274" fill="#34d399" font-size="7" text-anchor="middle" font-family="JetBrains Mono">output signal</text>

      <!-- Redis sentiment cache -->
      <rect x="480" y="148" width="120" height="52" rx="6" fill="#0f172a"/>
      <rect x="480" y="148" width="120" height="52" rx="6" fill="rgba(76,29,149,0.4)" stroke="#a78bfa" stroke-width="1.5"/>
      <text x="540" y="168" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">Redis</text>
      <text x="540" y="182" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">sentiment cache</text>
      <text x="540" y="194" fill="#a78bfa" font-size="7" text-anchor="middle" font-family="JetBrains Mono">fast read/write</text>

      <!-- MongoDB crypto_sentiment -->
      <rect x="638" y="228" width="100" height="52" rx="6" fill="#0f172a"/>
      <rect x="638" y="228" width="100" height="52" rx="6" fill="rgba(76,29,149,0.4)" stroke="#a78bfa" stroke-width="1.5"/>
      <text x="688" y="248" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">MongoDB</text>
      <text x="688" y="262" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">crypto_sentiment</text>
      <text x="688" y="274" fill="#a78bfa" font-size="7" text-anchor="middle" font-family="JetBrains Mono">persistence</text>

      <!-- ══════════════════════════════════════════════════════ -->
      <!-- ZONE: CORE ENGINE                                      -->
      <!-- ══════════════════════════════════════════════════════ -->
      <rect x="18" y="368" width="1020" height="290" rx="12" fill="none" stroke="#34d399" stroke-width="1" stroke-dasharray="8,4"/>
      <text x="30" y="363" fill="#34d399" font-size="9" font-family="JetBrains Mono" font-weight="600">CORE ENGINE — BinanceAutomation (Threaded)</text>

      <!-- Config -->
      <rect x="26" y="378" width="130" height="52" rx="6" fill="#0f172a"/>
      <rect x="26" y="378" width="130" height="52" rx="6" fill="rgba(30,41,59,0.5)" stroke="#94a3b8" stroke-width="1.5"/>
      <text x="91" y="398" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">Config</text>
      <text x="91" y="412" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">config/config.json</text>
      <text x="91" y="424" fill="#64748b" font-size="7" text-anchor="middle" font-family="JetBrains Mono">pairs · risk · leverage</text>

      <!-- MonitorThread -->
      <rect x="163" y="400" width="150" height="52" rx="6" fill="#0f172a"/>
      <rect x="163" y="400" width="150" height="52" rx="6" fill="rgba(136,19,55,0.4)" stroke="#fb7185" stroke-width="1.5"/>
      <text x="238" y="420" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">MonitorThread</text>
      <text x="238" y="434" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">every 300 s</text>
      <text x="238" y="446" fill="#fb7185" font-size="7" text-anchor="middle" font-family="JetBrains Mono">watchdog</text>

      <!-- Discord Alerts -->
      <rect x="280" y="330" width="110" height="42" rx="6" fill="#0f172a"/>
      <rect x="280" y="330" width="110" height="42" rx="6" fill="rgba(30,41,59,0.5)" stroke="#94a3b8" stroke-width="1.5"/>
      <text x="335" y="349" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">Discord</text>
      <text x="335" y="363" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">Alerts / Webhook</text>

      <!-- SignalAnalyzer -->
      <rect x="490" y="385" width="162" height="70" rx="6" fill="#0f172a"/>
      <rect x="490" y="385" width="162" height="70" rx="6" fill="rgba(6,78,59,0.4)" stroke="#34d399" stroke-width="1.5"/>
      <text x="571" y="408" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">SignalAnalyzer</text>
      <text x="571" y="422" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">Strategy Registry</text>
      <text x="571" y="436" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">15-min cycle · lazy-load</text>
      <text x="571" y="449" fill="#34d399" font-size="7" text-anchor="middle" font-family="JetBrains Mono">BTCUSDT · ETHUSDT · BCHUSDT</text>

      <!-- Strategies sub-label -->
      <rect x="494" y="455" width="68" height="16" rx="3" fill="rgba(6,78,59,0.3)" stroke="#34d399" stroke-width="0.8"/>
      <text x="528" y="467" fill="#34d399" font-size="7" text-anchor="middle" font-family="JetBrains Mono">ML Models</text>
      <rect x="580" y="455" width="68" height="16" rx="3" fill="rgba(6,78,59,0.3)" stroke="#34d399" stroke-width="0.8"/>
      <text x="614" y="467" fill="#34d399" font-size="7" text-anchor="middle" font-family="JetBrains Mono">RL Agents</text>

      <!-- MongoDB OHLCV -->
      <rect x="868" y="388" width="148" height="60" rx="6" fill="#0f172a"/>
      <rect x="868" y="388" width="148" height="60" rx="6" fill="rgba(76,29,149,0.4)" stroke="#a78bfa" stroke-width="1.5"/>
      <text x="942" y="412" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">MongoDB</text>
      <text x="942" y="426" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">OHLCV Data</text>
      <text x="942" y="440" fill="#a78bfa" font-size="7" text-anchor="middle" font-family="JetBrains Mono">candle store</text>

      <!-- OrderManager -->
      <rect x="672" y="460" width="148" height="72" rx="6" fill="#0f172a"/>
      <rect x="672" y="460" width="148" height="72" rx="6" fill="rgba(6,78,59,0.4)" stroke="#34d399" stroke-width="1.5"/>
      <text x="746" y="483" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">OrderManager</text>
      <text x="746" y="497" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">Binance Futures</text>
      <text x="746" y="511" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">risk · leverage · cooldown</text>
      <text x="746" y="525" fill="#34d399" font-size="7" text-anchor="middle" font-family="JetBrains Mono">SL/TP placement</text>

      <!-- Binance Futures API -->
      <rect x="868" y="465" width="148" height="60" rx="6" fill="#0f172a"/>
      <rect x="868" y="465" width="148" height="60" rx="6" fill="rgba(120,53,15,0.3)" stroke="#fbbf24" stroke-width="1.5"/>
      <text x="942" y="488" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">Binance</text>
      <text x="942" y="502" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">Futures API</text>
      <text x="942" y="516" fill="#fbbf24" font-size="7" text-anchor="middle" font-family="JetBrains Mono">REST · WebSocket</text>

      <!-- TradeChecker -->
      <rect x="490" y="545" width="162" height="60" rx="6" fill="#0f172a"/>
      <rect x="490" y="545" width="162" height="60" rx="6" fill="rgba(6,78,59,0.4)" stroke="#34d399" stroke-width="1.5"/>
      <text x="571" y="568" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">TradeChecker</text>
      <text x="571" y="582" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">SL/TP Monitor</text>
      <text x="571" y="596" fill="#34d399" font-size="7" text-anchor="middle" font-family="JetBrains Mono">live position mgmt</text>

      <!-- Redis trade/order state -->
      <rect x="668" y="590" width="150" height="52" rx="6" fill="#0f172a"/>
      <rect x="668" y="590" width="150" height="52" rx="6" fill="rgba(76,29,149,0.4)" stroke="#a78bfa" stroke-width="1.5"/>
      <text x="743" y="612" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">Redis</text>
      <text x="743" y="626" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">trade / order state</text>
      <text x="743" y="638" fill="#a78bfa" font-size="7" text-anchor="middle" font-family="JetBrains Mono">ephemeral cache</text>

      <!-- ContradictSimulator -->
      <rect x="458" y="540" width="0" height="0"/>
      <rect x="368" y="530" width="124" height="52" rx="6" fill="#0f172a"/>
      <rect x="368" y="530" width="124" height="52" rx="6" fill="rgba(30,41,59,0.5)" stroke="#94a3b8" stroke-width="1.5"/>
      <text x="430" y="549" fill="white" font-size="10" font-weight="600" text-anchor="middle" font-family="JetBrains Mono">Contradict</text>
      <text x="430" y="563" fill="#94a3b8" font-size="8" text-anchor="middle" font-family="JetBrains Mono">Simulator</text>
      <text x="430" y="577" fill="#64748b" font-size="7" text-anchor="middle" font-family="JetBrains Mono">research / backtesting</text>

      <!-- ══════════════════════════════════════════════════════ -->
      <!-- LEGEND                                                 -->
      <!-- ══════════════════════════════════════════════════════ -->
      <rect x="18" y="674" width="1020" height="52" rx="8" fill="rgba(15,23,42,0.6)" stroke="#1e293b" stroke-width="1"/>
      <text x="36" y="692" fill="#64748b" font-size="8" font-family="JetBrains Mono" font-weight="600">LEGEND</text>

      <!-- Frontend -->
      <rect x="100" y="680" width="14" height="14" rx="2" fill="rgba(8,51,68,0.4)" stroke="#22d3ee" stroke-width="1.5"/>
      <text x="120" y="692" fill="#94a3b8" font-size="8" font-family="JetBrains Mono">Intelligence</text>

      <rect x="210" y="680" width="14" height="14" rx="2" fill="rgba(6,78,59,0.4)" stroke="#34d399" stroke-width="1.5"/>
      <text x="230" y="692" fill="#94a3b8" font-size="8" font-family="JetBrains Mono">Core / Trading</text>

      <rect x="345" y="680" width="14" height="14" rx="2" fill="rgba(76,29,149,0.4)" stroke="#a78bfa" stroke-width="1.5"/>
      <text x="365" y="692" fill="#94a3b8" font-size="8" font-family="JetBrains Mono">Database</text>

      <rect x="455" y="680" width="14" height="14" rx="2" fill="rgba(120,53,15,0.3)" stroke="#fbbf24" stroke-width="1.5"/>
      <text x="475" y="692" fill="#94a3b8" font-size="8" font-family="JetBrains Mono">External / Cloud</text>

      <rect x="590" y="680" width="14" height="14" rx="2" fill="rgba(136,19,55,0.4)" stroke="#fb7185" stroke-width="1.5"/>
      <text x="610" y="692" fill="#94a3b8" font-size="8" font-family="JetBrains Mono">Monitoring</text>

      <rect x="700" y="680" width="14" height="14" rx="2" fill="rgba(30,41,59,0.5)" stroke="#94a3b8" stroke-width="1.5"/>
      <text x="720" y="692" fill="#94a3b8" font-size="8" font-family="JetBrains Mono">Generic</text>

      <!-- Line legend -->
      <line x1="800" y1="687" x2="830" y2="687" stroke="#64748b" stroke-width="1.2" marker-end="url(#arr)"/>
      <text x="836" y="692" fill="#94a3b8" font-size="8" font-family="JetBrains Mono">data flow</text>
      <line x1="920" y1="687" x2="950" y2="687" stroke="#64748b" stroke-width="1.2" stroke-dasharray="4,3" marker-end="url(#arr)"/>
      <text x="956" y="692" fill="#94a3b8" font-size="8" font-family="JetBrains Mono">async</text>

      <!-- Second legend row -->
      <text x="100" y="714" fill="#64748b" font-size="7" font-family="JetBrains Mono">threads: Signal Analysis (15 min) · Trade Checker (continuous) · Sentiment Cron (hourly) · Monitor (300 s)</text>
    </svg>
  </div>

  <!-- Summary cards -->
  <div class="cards">
    <div class="card">
      <div class="card-header">
        <div class="card-dot cyan"></div>
        <h3>Market Intelligence</h3>
      </div>
      <ul>
        <li>• Reddit &amp; News data ingestion</li>
        <li>• WeightedRedditAnalyzer + LLM scoring</li>
        <li>• VIX / Fear &amp; Greed market context</li>
        <li>• SentimentWorkflow — runs every hour via Croner</li>
        <li>• Scores cached in Redis, persisted in MongoDB</li>
      </ul>
    </div>
    <div class="card">
      <div class="card-header">
        <div class="card-dot emerald"></div>
        <h3>Core Trading Engine</h3>
      </div>
      <ul>
        <li>• BinanceAutomation — top-level orchestrator</li>
        <li>• SignalAnalyzer — lazy-loaded strategy registry</li>
        <li>• OrderManager — Futures orders with risk &amp; leverage</li>
        <li>• TradeChecker — live SL/TP lifecycle management</li>
        <li>• ContradictSimulator — research / backtesting</li>
      </ul>
    </div>
    <div class="card">
      <div class="card-header">
        <div class="card-dot violet"></div>
        <h3>Data &amp; Observability</h3>
      </div>
      <ul>
        <li>• MongoDB — OHLCV candles + sentiment persistence</li>
        <li>• Redis — trade/order state + sentiment cache</li>
        <li>• MonitorThread — watchdog every 300 s</li>
        <li>• Discord webhook alerts on thread failure</li>
        <li>• Pairs: BTC · ETH · BCH · BNB · SOL · XRP +more</li>
      </ul>
    </div>
  </div>

  <footer>ORBIT v0.x · Python 3.10+ · Poetry · MIT License · © 2026 Pankaj Kumar</footer>
</body>
</html>

## Acknowledgements

Orbit is in its early development phase and evolves with ongoing research, experimentation, and iteration. We appreciate everyone's ideas, feedback, and technical insights that help shape the system.

© 2026 Pankaj Kumar. All rights reserved.
