import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  BellRing,
  Bot,
  CandlestickChart,
  CheckCircle2,
  Clock3,
  Gauge,
  LayoutDashboard,
  Moon,
  Radio,
  RefreshCw,
  Server,
  ShieldCheck,
  Sun,
  TrendingUp,
  WalletCards,
  Wifi,
  Zap,
} from "lucide-react";
import "./App.css";

type NotificationField = { name: string; value: unknown; inline?: boolean };
type Notification = {
  id: string;
  channel: string;
  content: string;
  description: string;
  fields: NotificationField[];
  created_at: string;
};
type ServiceStatus = { status: string; version: string };
type Theme = "light" | "dark";
type View = "dashboard" | "discord";

const REFRESH_INTERVAL = 15_000;
const SIGNAL_CHANNELS = new Set([
  "signal",
  "chart_signal",
  "ai_predictions",
  "true_alarm",
  "average_alarm",
  "false_alarm",
]);
const TRADE_CHANNELS = new Set(["active_trades", "sl_update"]);
const PRICE_CHANNELS = new Set(["active_trade_prices", "levels_webhook"]);
const CRITICAL_CHANNELS = new Set(["alerts", "exception", "exception_params"]);
const CHANNEL_LABELS: Record<string, string> = {
  active_trade_prices: "Active prices",
  active_trades: "Active trades",
  ai_predictions: "AI prediction",
  alerts: "Alert",
  average_alarm: "Signal analysis",
  chart_signal: "Chart signal",
  cooldown: "Cooldown",
  exception: "Exception",
  exception_params: "Exception detail",
  false_alarm: "Rejected signal",
  levels_webhook: "Market level",
  logs: "System log",
  market_sentiment: "Market sentiment",
  params: "Strategy update",
  signal: "Signal",
  sl_update: "Stop update",
  true_alarm: "Confirmed signal",
  websocket: "WebSocket",
};

const formatChannel = (channel: string) =>
  CHANNEL_LABELS[channel] ?? channel.split("_").join(" ");
const notificationText = (item: Notification) =>
  [
    item.description,
    item.content,
    ...item.fields.map((field) => `${field.name}: ${String(field.value)}`),
  ]
    .filter(Boolean)
    .join(" · ");
const summaryText = (item: Notification) =>
  item.description ||
  item.content ||
  item.fields
    .map((field) => `${field.name}: ${String(field.value)}`)
    .join(" · ") ||
  "Update received";
const extractSymbol = (item: Notification) =>
  notificationText(item).match(/\b[A-Z]{2,10}(?:USDT|USD|BTC|ETH)\b/)?.[0] ??
  "MARKET";
const fieldValue = (item: Notification, pattern: RegExp) =>
  item.fields.find((field) => pattern.test(field.name))?.value;
const extractNumber = (value: unknown) =>
  String(value ?? "").match(/-?\$?\d[\d,.]*(?:\.\d+)?%?/)?.[0] ?? "—";
const toneForText = (value: string) =>
  /sell|short|bear|down|loss|negative|reject/i.test(value)
    ? "negative"
    : /buy|long|bull|up|profit|positive|confirm/i.test(value)
      ? "positive"
      : "neutral";

function timeAgo(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "recently";
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function EmptyPanel({ message }: { message: string }) {
  return (
    <div className="empty-panel">
      <span>Awaiting data</span>
      <p>{message}</p>
    </div>
  );
}

function App() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [serviceStatus, setServiceStatus] = useState<ServiceStatus | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [view, setView] = useState<View>("dashboard");
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem("orbit-theme");
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
  });

  const loadDashboard = useCallback(async () => {
    try {
      const [feedResult, statusResult] = await Promise.allSettled([
        fetch("/api/notifications?limit=250"),
        fetch("/api/status"),
      ]);
      if (feedResult.status === "rejected" || !feedResult.value.ok)
        throw new Error("The operations feed is unavailable.");
      const payload = (await feedResult.value.json()) as {
        notifications: Notification[];
      };
      setNotifications(payload.notifications);
      setError("");
      setServiceStatus(
        statusResult.status === "fulfilled" && statusResult.value.ok
          ? ((await statusResult.value.json()) as ServiceStatus)
          : null,
      );
      setLastUpdated(new Date());
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Could not load operations data.",
      );
      setServiceStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("orbit-theme", theme);
  }, [theme]);

  useEffect(() => {
    void loadDashboard();
    const interval = window.setInterval(
      () => void loadDashboard(),
      REFRESH_INTERVAL,
    );
    return () => window.clearInterval(interval);
  }, [loadDashboard]);

  const data = useMemo(() => {
    const byChannels = (channels: Set<string>) =>
      notifications.filter((item) => channels.has(item.channel));
    const trades = byChannels(TRADE_CHANNELS).slice(0, 4);
    const prices = byChannels(PRICE_CHANNELS).slice(0, 5);
    const signals = byChannels(SIGNAL_CHANNELS).slice(0, 4);
    const sentiment = notifications
      .filter((item) => item.channel === "market_sentiment")
      .slice(0, 3);
    const alerts = byChannels(CRITICAL_CHANNELS);
    const symbolCount = new Set(
      notifications.flatMap(
        (item) => notificationText(item).match(/\b[A-Z]{2,10}USDT\b/g) ?? [],
      ),
    ).size;
    return { trades, prices, signals, sentiment, alerts, symbolCount };
  }, [notifications]);

  const isConnected = lastUpdated !== null && !error;
  const isRuntimeOnline = serviceStatus?.status === "online";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button
          className="brand"
          onClick={() => setView("dashboard")}
          aria-label="Orbit dashboard home"
        >
          <span className="brand-symbol">
            <i />
            <i />
            <i />
          </span>
          <span>ORBIT</span>
        </button>
        <nav aria-label="Primary navigation">
          <button
            className={view === "dashboard" ? "nav-item active" : "nav-item"}
            onClick={() => setView("dashboard")}
          >
            <LayoutDashboard size={17} />
            <span>Command center</span>
          </button>
          <button
            className={view === "discord" ? "nav-item active" : "nav-item"}
            onClick={() => setView("discord")}
          >
            <BellRing size={17} />
            <span>Discord activity</span>
            <b>{notifications.length}</b>
          </button>
        </nav>
        <div className="sidebar-status">
          <span
            className={isRuntimeOnline ? "runtime-icon online" : "runtime-icon"}
          >
            <ShieldCheck size={18} />
          </span>
          <div>
            <small>Execution engine</small>
            <strong>
              {isRuntimeOnline ? "Protected & live" : "Status unknown"}
            </strong>
          </div>
        </div>
        <p className="version">
          ORBIT SYSTEMS · v{serviceStatus?.version ?? "1.0.0"}
        </p>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="mobile-logo">
            <span className="brand-symbol">
              <i />
              <i />
              <i />
            </span>
            <strong>ORBIT</strong>
          </div>
          <div className="mobile-navigation" aria-label="Mobile navigation">
            <button
              className={view === "dashboard" ? "active" : ""}
              onClick={() => setView("dashboard")}
              aria-label="Command center"
            >
              <LayoutDashboard size={15} />
            </button>
            <button
              className={view === "discord" ? "active" : ""}
              onClick={() => setView("discord")}
              aria-label="Discord activity"
            >
              <BellRing size={15} />
            </button>
          </div>
          <div className="view-name">
            <small>TRADING OPERATIONS</small>
            <strong>
              {view === "dashboard" ? "Command center" : "Discord activity"}
            </strong>
          </div>
          <div className="top-actions">
            <span className={`connection ${isConnected ? "" : "offline"}`}>
              <i />
              {isConnected ? "LIVE" : "OFFLINE"}
            </span>
            <span className="sync-time">
              <Clock3 size={14} />
              {lastUpdated
                ? lastUpdated.toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })
                : "Connecting"}
            </span>
            <button
              className="icon-button"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            >
              {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <button
              className="icon-button"
              onClick={() => void loadDashboard()}
              disabled={loading}
              aria-label="Refresh dashboard"
            >
              <RefreshCw size={17} className={loading ? "spinning" : ""} />
            </button>
          </div>
        </header>

        {view === "dashboard" ? (
          <main className="dashboard">
            <section className="dashboard-heading">
              <div>
                <span className="eyebrow">REALTIME OVERVIEW</span>
                <h1>Market command center</h1>
                <p>Everything that needs your attention, in one view.</p>
              </div>
              <div className="session-pill">
                <span
                  className={
                    isRuntimeOnline ? "status-orb online" : "status-orb"
                  }
                >
                  <Radio size={16} />
                </span>
                <div>
                  <small>SESSION STATUS</small>
                  <strong>
                    {isRuntimeOnline
                      ? "Systems operational"
                      : "Runtime unavailable"}
                  </strong>
                </div>
              </div>
            </section>
            {error && (
              <div className="error-banner" role="alert">
                <AlertTriangle size={17} />
                <span>
                  <strong>Live data unavailable.</strong> {error} Retrying
                  automatically.
                </span>
              </div>
            )}
            <section className="stat-strip" aria-label="Current trading status">
              <article>
                <span className="stat-icon purple">
                  <WalletCards size={17} />
                </span>
                <div>
                  <small>ACTIVE TRADES</small>
                  <strong>{data.trades.length}</strong>
                  <p>Current feed</p>
                </div>
              </article>
              <article>
                <span className="stat-icon blue">
                  <CandlestickChart size={17} />
                </span>
                <div>
                  <small>TRACKED MARKETS</small>
                  <strong>{data.symbolCount}</strong>
                  <p>USDT pairs detected</p>
                </div>
              </article>
              <article>
                <span className="stat-icon green">
                  <Zap size={17} />
                </span>
                <div>
                  <small>RECENT SIGNALS</small>
                  <strong>{data.signals.length}</strong>
                  <p>Latest analysis</p>
                </div>
              </article>
              <article>
                <span className="stat-icon amber">
                  <AlertTriangle size={17} />
                </span>
                <div>
                  <small>CRITICAL ALERTS</small>
                  <strong>{data.alerts.length}</strong>
                  <p>{data.alerts.length ? "Requires review" : "All clear"}</p>
                </div>
              </article>
            </section>
            <section className="operations-grid">
              <article className="panel trades-panel">
                <div className="panel-title">
                  <div>
                    <span className="panel-icon purple">
                      <WalletCards size={16} />
                    </span>
                    <div>
                      <h2>Active trades</h2>
                      <p>Open positions & stop updates</p>
                    </div>
                  </div>
                  <span className="live-label">
                    <i />
                    LIVE
                  </span>
                </div>
                {data.trades.length ? (
                  <div className="data-list">
                    {data.trades.map((item) => {
                      const tone = toneForText(notificationText(item));
                      return (
                        <div className="trade-row" key={item.id}>
                          <span className={`direction ${tone}`}>
                            {tone === "negative" ? (
                              <ArrowDownRight size={15} />
                            ) : (
                              <ArrowUpRight size={15} />
                            )}
                          </span>
                          <div>
                            <strong>{extractSymbol(item)}</strong>
                            <small>
                              {formatChannel(item.channel)} ·{" "}
                              {timeAgo(item.created_at)}
                            </small>
                          </div>
                          <p>
                            {extractNumber(
                              fieldValue(item, /price|entry|stop/i),
                            )}
                          </p>
                          <span className={`tone-text ${tone}`}>
                            {tone === "neutral" ? "UPDATE" : tone.toUpperCase()}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <EmptyPanel message="Open positions will appear here." />
                )}
              </article>
              <article className="panel prices-panel">
                <div className="panel-title">
                  <div>
                    <span className="panel-icon blue">
                      <CandlestickChart size={16} />
                    </span>
                    <div>
                      <h2>Active prices</h2>
                      <p>Latest market & trade levels</p>
                    </div>
                  </div>
                  <Activity size={16} />
                </div>
                {data.prices.length ? (
                  <div className="data-list">
                    {data.prices.map((item) => (
                      <div className="price-row" key={item.id}>
                        <div>
                          <strong>{extractSymbol(item)}</strong>
                          <small>{timeAgo(item.created_at)}</small>
                        </div>
                        <p>
                          {extractNumber(
                            fieldValue(item, /price|level|mark|current/i) ??
                              summaryText(item),
                          )}
                        </p>
                        <span
                          className={`mini-trend ${toneForText(notificationText(item))}`}
                        >
                          <TrendingUp size={14} />
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyPanel message="Live price updates will appear here." />
                )}
              </article>
              <article className="panel signals-panel">
                <div className="panel-title">
                  <div>
                    <span className="panel-icon green">
                      <BarChart3 size={16} />
                    </span>
                    <div>
                      <h2>Signal analysis</h2>
                      <p>Strategy decisions & confidence</p>
                    </div>
                  </div>
                  <Gauge size={16} />
                </div>
                {data.signals.length ? (
                  <div className="signal-list">
                    {data.signals.map((item) => {
                      const tone = toneForText(notificationText(item));
                      return (
                        <div className="signal-row" key={item.id}>
                          <div className="signal-meta">
                            <strong>{extractSymbol(item)}</strong>
                            <span className={`signal-badge ${tone}`}>
                              {formatChannel(item.channel)}
                            </span>
                            <time>{timeAgo(item.created_at)}</time>
                          </div>
                          <p>{summaryText(item)}</p>
                          <div className="confidence-track">
                            <span
                              className={tone}
                              style={{
                                width: `${Math.min(92, 45 + item.fields.length * 9)}%`,
                              }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <EmptyPanel message="Strategy signals and confirmations will appear here." />
                )}
              </article>
              <article className="panel sentiment-panel">
                <div className="panel-title">
                  <div>
                    <span className="panel-icon amber">
                      <Bot size={16} />
                    </span>
                    <div>
                      <h2>Market sentiment</h2>
                      <p>AI & news intelligence</p>
                    </div>
                  </div>
                  <span className="ai-label">AI</span>
                </div>
                {data.sentiment.length ? (
                  <div className="sentiment-list">
                    {data.sentiment.map((item) => {
                      const tone = toneForText(notificationText(item));
                      return (
                        <div className="sentiment-row" key={item.id}>
                          <div>
                            <strong>{extractSymbol(item)}</strong>
                            <span className={`sentiment-dot ${tone}`} />
                          </div>
                          <p>{summaryText(item)}</p>
                          <small>{timeAgo(item.created_at)}</small>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <EmptyPanel message="Sentiment intelligence will appear here." />
                )}
              </article>
              <article className="panel health-panel">
                <div className="panel-title">
                  <div>
                    <span className="panel-icon cyan">
                      <Server size={16} />
                    </span>
                    <div>
                      <h2>System health</h2>
                      <p>Runtime & data services</p>
                    </div>
                  </div>
                  <Wifi size={16} />
                </div>
                <div className="health-content">
                  <div
                    className={`health-score ${isRuntimeOnline && isConnected ? "healthy" : ""}`}
                  >
                    <strong>
                      {isRuntimeOnline && isConnected ? "100" : "—"}
                    </strong>
                    <small>HEALTH SCORE</small>
                  </div>
                  <div className="health-checks">
                    <div>
                      <span>
                        <CheckCircle2 size={14} />
                        Trading runtime
                      </span>
                      <strong className={isRuntimeOnline ? "good" : "muted"}>
                        {isRuntimeOnline ? "Operational" : "Unknown"}
                      </strong>
                    </div>
                    <div>
                      <span>
                        <CheckCircle2 size={14} />
                        Operations API
                      </span>
                      <strong className={isConnected ? "good" : "bad"}>
                        {isConnected ? "Connected" : "Offline"}
                      </strong>
                    </div>
                    <div>
                      <span>
                        <RefreshCw size={14} />
                        Refresh cadence
                      </span>
                      <strong>15 seconds</strong>
                    </div>
                  </div>
                </div>
              </article>
            </section>
          </main>
        ) : (
          <main className="discord-view">
            <section className="discord-heading">
              <div>
                <span className="eyebrow">DELIVERY LOG</span>
                <h1>Discord activity</h1>
                <p>
                  A separate, unfiltered record of successfully delivered
                  Discord events.
                </p>
              </div>
              <div className="event-total">
                <strong>{notifications.length}</strong>
                <span>EVENTS IN BUFFER</span>
              </div>
            </section>
            <section
              className="discord-feed"
              aria-live="polite"
              aria-busy={loading}
            >
              <div className="feed-header">
                <span>CHANNEL / EVENT</span>
                <span>DELIVERED</span>
              </div>
              {!loading && notifications.length === 0 ? (
                <EmptyPanel message="Discord deliveries will appear here." />
              ) : (
                notifications.map((item) => (
                  <article className="discord-event" key={item.id}>
                    <span
                      className={`event-dot ${toneForText(notificationText(item))}`}
                    />
                    <div>
                      <div className="event-meta">
                        <span>{formatChannel(item.channel)}</span>
                        <time>{timeAgo(item.created_at)}</time>
                      </div>
                      <h3>{summaryText(item)}</h3>
                      {item.content && item.description && (
                        <p>{item.content}</p>
                      )}
                      {item.fields.length > 0 && (
                        <dl>
                          {item.fields.map((field, index) => (
                            <div key={`${field.name}-${index}`}>
                              <dt>{field.name}</dt>
                              <dd>{String(field.value)}</dd>
                            </div>
                          ))}
                        </dl>
                      )}
                    </div>
                  </article>
                ))
              )}
            </section>
          </main>
        )}
      </div>
    </div>
  );
}

export default App;
