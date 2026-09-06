import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, BrainCircuit, CheckCircle2, Clock3, FileWarning, Gauge, ListFilter, Moon, Radio, RefreshCw, Search, ShieldCheck, Sun, TerminalSquare, WalletCards, Wifi, Zap } from "lucide-react";
import "./App.css";

type RuntimeNode = { runtime_id: string; status: string; heartbeat_at: string | null };
type RuntimeState = { status: string; current_activity: string | null; detail: string | null; updated_at: string | null; runtimes: RuntimeNode[] };
type Position = { trade_id: string; symbol: string; side: string; quantity: number | null; entry_price: number | null; current_price: number | null; unrealized_pnl: number | null; stop_loss: number | null; take_profit: number | null; protection_status: string; execution_mode: string; entered_at: string | null };
type Signal = { decision_id: string; symbol: string; signal: string | null; outcome: string | null; reason: string | null; pattern: string | null; sentiment: string | null; execution_mode: string; entry_price: number | null; stop_loss: number | null; take_profit: number | null; analyzed_at: string | null; latest_status: string | null };
type Sentiment = { effective: string | null; observed: string | null; confidence: number | null; provider: string | null; explanation: string | null; action: string | null; updated_at: string | null };
type SentimentHistory = Pick<Sentiment, "effective" | "observed" | "confidence" | "provider" | "explanation" | "action" | "updated_at">;
type RiskExecution = { active_modes: string[]; can_submit_orders: boolean; risk_limits: Record<string, unknown> };
type LogEntry = { id: string; level: string; message: string; context: string | null; created_at: string };
type ExceptionEntry = { id: string; type: string; message: string; context: string | null; traceback: string | null; created_at: string };
type CommandCenter = { generated_at: string; runtime: RuntimeState; positions: Position[]; signals: Signal[]; sentiment: Sentiment; sentiment_history: SentimentHistory[]; risk_execution: RiskExecution; logs: LogEntry[]; exceptions: ExceptionEntry[] };
type Theme = "light" | "dark";

const REFRESH_INTERVAL = 5_000;
const number = (value: number | null, digits = 2) => value == null ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
const price = (value: number | null) => number(value, value != null && value < 10 ? 4 : 2);
const time = (value: string | null) => value ? new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
const age = (value: string | null) => { if (!value) return "—"; const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000)); if (seconds < 60) return `${seconds}s`; if (seconds < 3600) return `${Math.floor(seconds / 60)}m`; if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`; return `${Math.floor(seconds / 86400)}d`; };
const tone = (value: string | null | undefined) => { const text = value ?? ""; if (/error|critical|fail|reject|bear|sell|short|negative|unprotected/i.test(text)) return "negative"; if (/warn|pending|wait|neutral|hold|unverified/i.test(text)) return "warning"; if (/pass|approve|bull|buy|long|positive|protected|online|healthy|position/i.test(text)) return "positive"; return "neutral"; };
const initialTheme = (): Theme => { const stored = localStorage.getItem("orbit-theme"); return stored === "light" || stored === "dark" ? stored : "dark"; };

function Empty({ children }: { children: string }) { return <div className="empty"><Radio size={19} /><strong>No live data</strong><span>{children}</span></div>; }

function App() {
  const [snapshot, setSnapshot] = useState<CommandCenter | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("ALL");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = useCallback(async () => { try { const response = await fetch("/api/command-center?signal_limit=25&log_limit=100&exception_limit=25"); if (!response.ok) throw new Error(response.status === 503 ? "Operational data store is unavailable." : `Command center request failed (${response.status}).`); setSnapshot(await response.json() as CommandCenter); setError(""); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Live state is unavailable."); } finally { setLoading(false); } }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("orbit-theme", theme); }, [theme]);
  useEffect(() => { void load(); if (!autoRefresh) return; const timer = window.setInterval(() => void load(), REFRESH_INTERVAL); return () => window.clearInterval(timer); }, [load, autoRefresh]);

  const logs = useMemo(() => (snapshot?.logs ?? []).filter((item) => (level === "ALL" || item.level.toUpperCase() === level) && `${item.message} ${item.context ?? ""}`.toLowerCase().includes(query.toLowerCase())), [snapshot, level, query]);
  const sentimentHistory = snapshot?.sentiment_history?.length ? snapshot.sentiment_history : snapshot?.sentiment.updated_at ? [snapshot.sentiment] : [];
  const runtimeStatus = snapshot?.runtime.status ?? (loading ? "connecting" : "unknown");
  const apiConnected = snapshot != null && !error;
  const live = runtimeStatus === "online" && apiConnected;
  const protectedCount = snapshot?.positions.filter((item) => item.protection_status.toLowerCase() === "protected").length ?? 0;
  const pnl = snapshot?.positions.reduce((sum, item) => sum + (item.unrealized_pnl ?? 0), 0) ?? 0;
  const modes = snapshot?.risk_execution.active_modes ?? [];

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#top"><span className="brand-symbol"><i /><i /><i /></span><span>ORBIT</span></a>
      <nav aria-label="Command center sections">
        <a className="nav-item active" href="#top"><Zap size={17} />Live command</a>
        <a className="nav-item" href="#positions"><WalletCards size={17} />Positions <b>{snapshot?.positions.length ?? 0}</b></a>
        <a className="nav-item" href="#signals"><Activity size={17} />Signals</a>
        <a className="nav-item" href="#sentiment"><BrainCircuit size={17} />Intelligence</a>
        <a className="nav-item" href="#logs"><TerminalSquare size={17} />Logs</a>
        <a className="nav-item" href="#exceptions"><FileWarning size={17} />Exceptions <b className={snapshot?.exceptions.length ? "warn" : ""}>{snapshot?.exceptions.length ?? 0}</b></a>
      </nav>
      <div className="system-status"><small>SYSTEM STATUS</small>{(snapshot?.runtime.runtimes ?? []).map((node) => <div key={node.runtime_id}><i className={tone(node.status)} /><span>{node.runtime_id}</span><strong className={tone(node.status)}>{node.status}</strong></div>)}<div><i className={apiConnected ? "positive" : loading ? "warning" : "negative"} /><span>Data API</span><strong className={apiConnected ? "positive" : loading ? "warning" : "negative"}>{apiConnected ? "Connected" : loading ? "Connecting" : "Unavailable"}</strong></div></div>
    </aside>

    <div className="workspace" id="top">
      <header className="topbar"><div className="view-name"><small>TRADING OPERATIONS</small><strong>Live command center</strong></div><div className="top-actions"><span className={`connection ${live ? "" : apiConnected ? "degraded" : "offline"}`}><i />{live ? "LIVE · 5s" : apiConnected ? `API CONNECTED · ${runtimeStatus.toUpperCase()}` : loading ? "CONNECTING" : "UNAVAILABLE"}</span><span className={`mode-badge ${modes.includes("live") ? "live-mode" : ""}`}><ShieldCheck size={13} />{modes.length ? modes.join(" + ").toUpperCase() : "MODE UNKNOWN"}</span><span className="sync-time"><Clock3 size={14} />{time(snapshot?.generated_at ?? null)}</span><button className="icon-button" onClick={() => setAutoRefresh((value) => !value)} title="Toggle auto refresh"><Radio size={16} className={autoRefresh ? "pulse" : ""} /></button><button className="icon-button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</button><button className="icon-button" onClick={() => void load()} disabled={loading} aria-label="Refresh"><RefreshCw size={17} className={loading ? "spinning" : ""} /></button></div></header>

      <main className="command-center">
        {error && <div className="error-banner"><AlertTriangle size={16} /><div><strong>Live state unavailable</strong><span>{error} Showing the last successful snapshot.</span></div></div>}
        <section className="now-strip"><span className={`now-orb ${live ? "online" : ""}`}><Activity size={20} /></span><div className="now-copy"><small>ORBIT IS NOW</small><strong>{snapshot?.runtime.current_activity ?? (loading ? "Connecting to the trading runtime…" : "Waiting for runtime activity")}</strong><p>{snapshot?.runtime.detail ?? "The runtime has not published a current activity detail."}</p></div><div className="now-meta"><span><small>LAST PROGRESS</small><strong>{age(snapshot?.runtime.updated_at ?? null)} ago</strong></span><span><small>RUNTIME</small><strong className={live ? "positive" : "negative"}>{snapshot?.runtime.status ?? "unknown"}</strong></span></div></section>

        <section className="primary-grid">
          <article className="panel positions-panel" id="positions"><header className="panel-header"><div><WalletCards size={17} /><span><h2>Active positions <b>{snapshot?.positions.length ?? 0}</b></h2><p>Exchange-backed position state and protection</p></span></div><strong className={pnl >= 0 ? "positive" : "negative"}>{pnl >= 0 ? "+" : ""}{number(pnl)} USDT</strong></header>{!snapshot?.positions.length ? <Empty>There are no active positions in Redis.</Empty> : <div className="table-scroll"><table><thead><tr><th>Position</th><th>Size</th><th>Entry</th><th>Mark</th><th>P&amp;L</th><th>Stop / Target</th><th>Protection</th><th>Age</th></tr></thead><tbody>{snapshot.positions.map((item) => <tr key={item.trade_id}><td><strong>{item.symbol}</strong><span className={tone(item.side)}>{item.side} · {item.execution_mode}</span></td><td>{number(item.quantity, 6)}</td><td>{price(item.entry_price)}</td><td>{price(item.current_price)}</td><td className={(item.unrealized_pnl ?? 0) >= 0 ? "positive" : "negative"}><strong>{item.unrealized_pnl != null && item.unrealized_pnl >= 0 ? "+" : ""}{number(item.unrealized_pnl)}</strong></td><td><strong>{price(item.stop_loss)}</strong><span>{price(item.take_profit)}</span></td><td><span className={`status-chip ${tone(item.protection_status)}`}>{item.protection_status || "unknown"}</span></td><td>{age(item.entered_at)}</td></tr>)}</tbody></table></div>}</article>

          <article className="panel sentiment-panel" id="sentiment"><header className="panel-header"><div><BrainCircuit size={17} /><span><h2>Market sentiment · 24h</h2><p>Latest completed intelligence runs</p></span></div><small>{sentimentHistory.length} observations</small></header>{!sentimentHistory.length ? <Empty>No sentiment result has been published.</Empty> : <div className="sentiment-history">{sentimentHistory.map((item, index) => <div className={`sentiment-entry ${index === 0 ? "latest" : ""}`} key={`${item.updated_at}-${index}`}><div className={`sentiment-value ${tone(item.effective ?? item.observed)}`}><strong>{item.confidence == null ? "—" : `${Math.round(item.confidence * 100)}%`}</strong><span>{item.effective ?? item.observed ?? "Unknown"}</span></div><div><strong>{item.observed ?? item.effective ?? "Unknown"}{index === 0 && <b>LATEST</b>}</strong><p>{item.explanation ?? "No explanation provided."}</p><small>{item.provider ?? "Unknown provider"} · {age(item.updated_at)} ago</small></div></div>)}</div>}</article>

          <article className="panel signals-panel" id="signals"><header className="panel-header"><div><Activity size={17} /><span><h2>Signals analyzed</h2><p>Persisted strategy decisions—not delivery events</p></span></div><strong>{snapshot?.signals.length ?? 0} recent</strong></header>{!snapshot?.signals.length ? <Empty>No strategy decisions were found in MongoDB.</Empty> : <div className="table-scroll"><table><thead><tr><th>Market</th><th>Signal</th><th>Pattern</th><th>Sentiment</th><th>Decision</th><th>Reason</th><th>Analyzed</th></tr></thead><tbody>{snapshot.signals.map((item) => <tr key={item.decision_id}><td><strong>{item.symbol}</strong><span>{item.execution_mode}</span></td><td className={tone(item.signal)}>{item.signal ?? "—"}</td><td>{item.pattern ?? "—"}</td><td><span className={`status-chip ${tone(item.sentiment)}`}>{item.sentiment ?? "unknown"}</span></td><td><strong className={tone(item.latest_status ?? item.outcome)}>{item.latest_status ?? item.outcome ?? "analyzed"}</strong></td><td className="reason-cell" title={item.reason ?? ""}>{item.reason ?? "—"}</td><td>{time(item.analyzed_at)}</td></tr>)}</tbody></table></div>}</article>

          <article className="panel risk-panel"><header className="panel-header"><div><Gauge size={17} /><span><h2>Risk &amp; execution</h2><p>Current safety posture</p></span></div><Wifi size={15} /></header><div className="risk-grid"><div><small>ORDER SUBMISSION</small><strong className={snapshot?.risk_execution.can_submit_orders ? "positive" : "negative"}>{snapshot?.risk_execution.can_submit_orders ? "Enabled" : "Blocked"}</strong></div><div><small>POSITIONS PROTECTED</small><strong className={protectedCount === (snapshot?.positions.length ?? 0) ? "positive" : "warning"}>{protectedCount} / {snapshot?.positions.length ?? 0}</strong></div><div><small>UNREALIZED P&amp;L</small><strong className={pnl >= 0 ? "positive" : "negative"}>{pnl >= 0 ? "+" : ""}{number(pnl)}</strong></div><div><small>ACTIVE MODE</small><strong>{modes.join(", ") || "Unknown"}</strong></div></div><div className="limits"><small>CONFIGURED LIMITS</small>{Object.entries(snapshot?.risk_execution.risk_limits ?? {}).length ? Object.entries(snapshot?.risk_execution.risk_limits ?? {}).map(([key, value]) => <div key={key}><span>{key.replace(/_/g, " ")}</span><strong>{String(value)}</strong></div>) : <p>No risk limits were exposed by configuration.</p>}</div></article>

          <article className="panel logs-panel" id="logs"><header className="panel-header"><div><TerminalSquare size={17} /><span><h2>Live logs</h2><p>Runtime observability stream</p></span></div><div className="log-controls"><label><Search size={13} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search logs…" /></label><label><ListFilter size={13} /><select value={level} onChange={(event) => setLevel(event.target.value)}><option>ALL</option><option>DEBUG</option><option>INFO</option><option>WARNING</option><option>ERROR</option><option>CRITICAL</option></select></label></div></header>{!logs.length ? <Empty>{snapshot?.logs.length ? "No logs match the current filters." : "The runtime log stream is empty."}</Empty> : <div className="log-list">{logs.map((item) => <div key={item.id}><time>{time(item.created_at)}</time><span className={`log-level ${tone(item.level)}`}>{item.level}</span><p>{item.message}{item.context && <small> · {item.context}</small>}</p></div>)}</div>}</article>

          <article className="panel exceptions-panel" id="exceptions"><header className="panel-header"><div><FileWarning size={17} /><span><h2>Exceptions <b>{snapshot?.exceptions.length ?? 0}</b></h2><p>Failures requiring investigation</p></span></div>{snapshot?.exceptions.length ? <AlertTriangle className="warning" size={16} /> : <CheckCircle2 className="positive" size={16} />}</header>{!snapshot?.exceptions.length ? <div className="all-clear"><CheckCircle2 size={27} /><strong>No exceptions</strong><span>No runtime failures are currently recorded.</span></div> : <div className="exception-list">{snapshot.exceptions.map((item) => <details key={item.id}><summary><AlertTriangle size={15} /><span><strong>{item.type}</strong><small>{item.message}</small></span><time>{age(item.created_at)} ago</time></summary>{(item.context || item.traceback) && <pre>{item.context}{item.traceback ? `\n${item.traceback}` : ""}</pre>}</details>)}</div>}</article>
        </section>
      </main>
    </div>
  </div>;
}

export default App;
