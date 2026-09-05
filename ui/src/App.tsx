import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertTriangle, BellRing, Bot, ChevronRight, CircleDot,
  Clock3, Command, LayoutDashboard, Radio, RefreshCw, Search,
  Server, ShieldCheck, Sparkles, TrendingUp, Wifi, Zap,
} from 'lucide-react'
import './App.css'

type NotificationField = { name: string; value: unknown; inline?: boolean }
type Notification = { id: string; channel: string; content: string; description: string; fields: NotificationField[]; created_at: string }
type ServiceStatus = { status: string; version: string }

const REFRESH_INTERVAL = 15_000
const CRITICAL_CHANNELS = new Set(['alerts', 'exception', 'exception_params'])
const SIGNAL_CHANNELS = new Set(['signal', 'chart_signal', 'ai_predictions', 'true_alarm'])
const CHANNEL_LABELS: Record<string, string> = {
  active_trade_prices: 'Trade prices', active_trades: 'Active trades', ai_predictions: 'AI predictions', alerts: 'Alerts',
  average_alarm: 'Average alarms', chart_signal: 'Charts', cooldown: 'Cooldowns', exception: 'Exceptions',
  exception_params: 'Exception details', false_alarm: 'False alarms', levels_webhook: 'Market levels', logs: 'System logs',
  market_sentiment: 'Sentiment', params: 'Strategy params', signal: 'Signals', sl_update: 'Stop-loss updates',
  true_alarm: 'True alarms', websocket: 'WebSocket',
}
const CHANNEL_TONES: Record<string, string> = {
  alerts: 'critical', exception: 'critical', exception_params: 'critical', true_alarm: 'positive', signal: 'signal',
  chart_signal: 'signal', ai_predictions: 'signal', market_sentiment: 'sentiment', false_alarm: 'muted',
  average_alarm: 'warning', sl_update: 'warning', cooldown: 'warning', active_trades: 'positive',
}

const formatChannel = (channel: string) => CHANNEL_LABELS[channel] ?? channel.split('_').join(' ')
const isRecent = (value: string, hours: number) => Date.now() - new Date(value).getTime() <= hours * 3_600_000
function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(date)
}
function timeAgo(value: string) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}
function extractSymbols(notification: Notification) {
  const text = [notification.content, notification.description, ...notification.fields.flatMap((field) => [field.name, String(field.value)])].join(' ')
  return Array.from(new Set(text.match(/\b[A-Z]{2,10}USDT\b/g) ?? []))
}

function App() {
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [serviceStatus, setServiceStatus] = useState<ServiceStatus | null>(null)
  const [selectedChannel, setSelectedChannel] = useState('all')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const loadDashboard = useCallback(async () => {
    try {
      const [feedResult, statusResult] = await Promise.allSettled([fetch('/api/notifications?limit=250'), fetch('/api/status')])
      if (feedResult.status === 'rejected' || !feedResult.value.ok) throw new Error('The notification service is unavailable.')
      const payload = await feedResult.value.json() as { notifications: Notification[] }
      setNotifications(payload.notifications)
      setError('')
      if (statusResult.status === 'fulfilled' && statusResult.value.ok) setServiceStatus(await statusResult.value.json() as ServiceStatus)
      else setServiceStatus(null)
      setLastUpdated(new Date())
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Could not load notifications.')
      setServiceStatus(null)
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    void loadDashboard()
    const interval = window.setInterval(() => void loadDashboard(), REFRESH_INTERVAL)
    return () => window.clearInterval(interval)
  }, [loadDashboard])

  const channels = useMemo(() => Array.from(new Set(notifications.map((item) => item.channel))).sort(), [notifications])
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return notifications.filter((item) => {
      const channelMatches = selectedChannel === 'all' || item.channel === selectedChannel
      const text = [item.content, item.description, item.channel, ...item.fields.flatMap((field) => [field.name, String(field.value)])].join(' ').toLowerCase()
      return channelMatches && (!needle || text.includes(needle))
    })
  }, [notifications, query, selectedChannel])
  const metrics = useMemo(() => {
    const recent = notifications.filter((item) => isRecent(item.created_at, 24))
    return {
      recent: recent.length,
      signals: recent.filter((item) => SIGNAL_CHANNELS.has(item.channel)).length,
      critical: recent.filter((item) => CRITICAL_CHANNELS.has(item.channel)).length,
      tradeEvents: recent.filter((item) => ['active_trades', 'active_trade_prices', 'sl_update'].includes(item.channel)).length,
    }
  }, [notifications])
  const activity = useMemo(() => {
    const counts = new Map<string, number>()
    notifications.filter((item) => isRecent(item.created_at, 24)).forEach((item) => counts.set(item.channel, (counts.get(item.channel) ?? 0) + 1))
    return Array.from(counts, ([channel, count]) => ({ channel, count })).sort((a, b) => b.count - a.count).slice(0, 5)
  }, [notifications])
  const symbols = useMemo(() => {
    const counts = new Map<string, { count: number; lastSeen: string }>()
    notifications.forEach((notification) => extractSymbols(notification).forEach((symbol) => {
      const current = counts.get(symbol)
      counts.set(symbol, { count: (current?.count ?? 0) + 1, lastSeen: current?.lastSeen ?? notification.created_at })
    }))
    return Array.from(counts, ([symbol, values]) => ({ symbol, ...values })).sort((a, b) => b.count - a.count).slice(0, 5)
  }, [notifications])
  const maxActivity = Math.max(1, ...activity.map((item) => item.count))
  const latestEvent = notifications[0]
  const isConnected = !error
  const isRuntimeOnline = serviceStatus?.status === 'online'

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#overview" aria-label="Orbit dashboard home"><span className="brand-mark"><span /></span><span>ORBIT</span></a>
      <nav aria-label="Dashboard sections">
        <a className="nav-item active" href="#overview"><LayoutDashboard size={17} /><span>Overview</span></a>
        <a className="nav-item" href="#activity"><Radio size={17} /><span>Live activity</span><span className="nav-count">{notifications.length}</span></a>
        <a className="nav-item" href="#signals"><Zap size={17} /><span>Signals</span></a>
        <a className="nav-item" href="#systems"><Server size={17} /><span>Systems</span></a>
      </nav>
      <div className="sidebar-bottom">
        <div className="environment-card"><span className="environment-icon"><ShieldCheck size={17} /></span><div><span>Execution</span><strong>Guardrails active</strong></div></div>
        <p>Orbit signal operations<br />v{serviceStatus?.version ?? '1.0.0'}</p>
      </div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <div className="mobile-brand"><span className="brand-mark"><span /></span><span>ORBIT</span></div>
        <div className="topbar-copy"><span className="breadcrumb">Operations <ChevronRight size={13} /> Overview</span><strong>Command center</strong></div>
        <div className="topbar-actions"><div className="topbar-status"><span className={isConnected ? 'status-dot' : 'status-dot offline'} />{isConnected ? 'Live data' : 'Feed offline'}</div><button className="icon-button" onClick={() => void loadDashboard()} disabled={loading} aria-label="Refresh dashboard"><RefreshCw size={17} className={loading ? 'spinning' : ''} /></button></div>
      </header>
      <main id="overview">
        <section className="page-heading">
          <div><p className="eyebrow"><Sparkles size={13} /> Realtime intelligence</p><h1>Markets move fast.<br /><span>Stay in orbit.</span></h1><p className="lede">One operational view for every signal, trade update, alert, and system event.</p></div>
          <div className="sync-card"><span className="pulse-ring"><Radio size={17} /></span><div><span>Last synchronized</span><strong>{lastUpdated ? lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : 'Connecting…'}</strong></div></div>
        </section>
        {error && <div className="error-banner" role="alert"><AlertTriangle size={17} /><div><strong>Live feed unavailable</strong><span>{error} Retrying automatically.</span></div></div>}
        <section className="metrics-grid" aria-label="Last 24 hours overview">
          <article className="metric-card accent-green"><div className="metric-top"><span>Events / 24h</span><Activity size={18} /></div><strong>{metrics.recent}</strong><p><TrendingUp size={13} /> Captured in the live buffer</p></article>
          <article className="metric-card accent-blue"><div className="metric-top"><span>Signal events</span><Zap size={18} /></div><strong>{metrics.signals}</strong><p><CircleDot size={13} /> Analysis and confirmations</p></article>
          <article className="metric-card accent-amber"><div className="metric-top"><span>Trade updates</span><BellRing size={18} /></div><strong>{metrics.tradeEvents}</strong><p><Clock3 size={13} /> Prices, positions and stops</p></article>
          <article className="metric-card accent-red"><div className="metric-top"><span>Critical alerts</span><AlertTriangle size={18} /></div><strong>{metrics.critical}</strong><p className={metrics.critical ? 'attention' : ''}><ShieldCheck size={13} /> {metrics.critical ? 'Review recommended' : 'No action required'}</p></article>
        </section>
        <section className="insight-grid" id="signals">
          <article className="panel activity-panel"><div className="panel-heading"><div><span className="section-kicker">24 hour pulse</span><h2>Activity by channel</h2></div><Activity size={18} /></div>{activity.length ? <div className="activity-list">{activity.map((item) => <div className="activity-row" key={item.channel}><div><span>{formatChannel(item.channel)}</span><strong>{item.count}</strong></div><div className="activity-track"><span className={CHANNEL_TONES[item.channel] ?? 'default'} style={{ width: `${Math.max(8, item.count / maxActivity * 100)}%` }} /></div></div>)}</div> : <div className="panel-empty">Activity will appear after the first event.</div>}</article>
          <article className="panel watch-panel"><div className="panel-heading"><div><span className="section-kicker">Market radar</span><h2>Most active symbols</h2></div><Command size={18} /></div>{symbols.length ? <div className="symbol-list">{symbols.map((item, index) => <div className="symbol-row" key={item.symbol}><span className="symbol-rank">{String(index + 1).padStart(2, '0')}</span><div><strong>{item.symbol.replace('USDT', '')}<em>/USDT</em></strong><span>Last event {timeAgo(item.lastSeen)}</span></div><span className="symbol-events">{item.count} event{item.count === 1 ? '' : 's'}</span></div>)}</div> : <div className="panel-empty">Symbols are detected from incoming events.</div>}</article>
          <article className="panel systems-panel" id="systems"><div className="panel-heading"><div><span className="section-kicker">Infrastructure</span><h2>System health</h2></div><Server size={18} /></div><div className="health-status"><span className={isRuntimeOnline ? 'health-orb online' : 'health-orb'}><Wifi size={22} /></span><div><span>Orbit runtime</span><strong>{isRuntimeOnline ? 'Operational' : 'Status unavailable'}</strong></div></div><div className="health-list"><div><span>Notification API</span><strong className={isConnected ? 'good' : 'bad'}>{isConnected ? 'Connected' : 'Disconnected'}</strong></div><div><span>Discord delivery</span><strong className="good">Preserved</strong></div><div><span>Refresh cadence</span><strong>15 seconds</strong></div></div></article>
        </section>
        <section className="feed-section" id="activity">
          <div className="feed-title"><div><span className="section-kicker">Event stream</span><h2>Live activity</h2><p>Every successful Discord delivery, mirrored here in realtime.</p></div>{latestEvent && <div className="latest-badge"><span />Latest event {timeAgo(latestEvent.created_at)}</div>}</div>
          <div className="toolbar" aria-label="Notification filters"><label className="search-box"><Search size={16} /><span className="sr-only">Search notifications</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search symbols, events, values…" /></label><div className="channel-tabs"><button className={selectedChannel === 'all' ? 'active' : ''} onClick={() => setSelectedChannel('all')}>All</button>{channels.slice(0, 4).map((channel) => <button className={selectedChannel === channel ? 'active' : ''} onClick={() => setSelectedChannel(channel)} key={channel}>{formatChannel(channel)}</button>)}</div><label className="channel-select"><span className="sr-only">Choose a channel</span><select value={selectedChannel} onChange={(event) => setSelectedChannel(event.target.value)}><option value="all">All channels</option>{channels.map((channel) => <option value={channel} key={channel}>{formatChannel(channel)}</option>)}</select></label><div className="feed-count"><strong>{filtered.length}</strong><span>events</span></div></div>
          <div className="feed" aria-live="polite" aria-busy={loading}><div className="feed-header"><span>Latest first</span><span>{loading ? 'Synchronizing…' : 'Auto-refresh enabled'}</span></div>{!loading && filtered.length === 0 ? <div className="empty-state"><span className="empty-orbit"><Bot size={28} /></span><h3>No events match this view</h3><p>{notifications.length ? 'Try another channel or search term.' : 'New Discord notifications will appear here automatically.'}</p></div> : filtered.map((notification) => <article className={`notification-card ${CHANNEL_TONES[notification.channel] ?? 'default'}`} key={notification.id}><div className="event-rail"><span className="event-dot" /></div><div className="event-body"><div className="event-meta"><span className="channel-pill">{formatChannel(notification.channel)}</span><time dateTime={notification.created_at}>{formatTime(notification.created_at)} · {timeAgo(notification.created_at)}</time></div>{notification.content && <p className="event-content">{notification.content}</p>}{notification.description && <h3>{notification.description}</h3>}{notification.fields.length > 0 && <dl className="fields-grid">{notification.fields.map((field, index) => <div key={`${field.name}-${index}`}><dt>{field.name}</dt><dd>{String(field.value)}</dd></div>)}</dl>}</div></article>)}</div>
        </section>
      </main>
      <footer><span>ORBIT / SIGNAL OPERATIONS</span><span>Discord delivery remains active</span></footer>
    </div>
  </div>
}

export default App
