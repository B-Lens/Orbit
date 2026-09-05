import { useCallback, useEffect, useMemo, useState } from 'react'
import './App.css'

type NotificationField = { name: string; value: unknown; inline?: boolean }
type Notification = { id: string; channel: string; content: string; description: string; fields: NotificationField[]; created_at: string }

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
  average_alarm: 'warning', sl_update: 'warning', cooldown: 'warning',
}

const formatChannel = (channel: string) => CHANNEL_LABELS[channel] ?? channel.split('_').join(' ')
function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(date)
}

function App() {
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [selectedChannel, setSelectedChannel] = useState('all')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const loadNotifications = useCallback(async () => {
    try {
      const response = await fetch('/api/notifications?limit=250')
      if (!response.ok) throw new Error('The notification service is unavailable.')
      const payload = await response.json() as { notifications: Notification[] }
      setNotifications(payload.notifications)
      setError('')
      setLastUpdated(new Date())
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Could not load notifications.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    void loadNotifications()
    const interval = window.setInterval(() => void loadNotifications(), 15_000)
    return () => window.clearInterval(interval)
  }, [loadNotifications])

  const channels = useMemo(() => Array.from(new Set(notifications.map((item) => item.channel))).sort(), [notifications])
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return notifications.filter((item) => {
      const channelMatches = selectedChannel === 'all' || item.channel === selectedChannel
      const text = [item.content, item.description, item.channel, ...item.fields.flatMap((field) => [field.name, String(field.value)])].join(' ').toLowerCase()
      return channelMatches && (!needle || text.includes(needle))
    })
  }, [notifications, query, selectedChannel])

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="#top" aria-label="Orbit home"><span className="brand-mark">O</span><span>ORBIT</span></a>
      <div className="topbar-status"><span className={error ? 'status-dot offline' : 'status-dot'} />{error ? 'Feed disconnected' : 'Live feed'}</div>
    </header>
    <main id="top">
      <section className="page-heading">
        <div><p className="eyebrow">Operations console</p><h1>Notification feed</h1><p className="lede">Every event delivered to Discord, collected in one calm, searchable timeline.</p></div>
        <button className="refresh-button" onClick={() => void loadNotifications()} disabled={loading}><span aria-hidden="true">↻</span> {loading ? 'Syncing' : 'Refresh'}</button>
      </section>
      <section className="toolbar" aria-label="Notification filters">
        <label className="search-box"><span aria-hidden="true">⌕</span><span className="sr-only">Search notifications</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search symbols, events, values…" /></label>
        <label className="channel-select"><span>Channel</span><select value={selectedChannel} onChange={(event) => setSelectedChannel(event.target.value)}><option value="all">All channels</option>{channels.map((channel) => <option value={channel} key={channel}>{formatChannel(channel)}</option>)}</select></label>
        <div className="feed-count"><strong>{filtered.length}</strong><span>visible events</span></div>
      </section>
      {error && <div className="error-banner" role="alert"><strong>Feed unavailable.</strong> {error} The dashboard will retry automatically.</div>}
      <section className="feed" aria-live="polite" aria-busy={loading}>
        <div className="feed-header"><span>Latest first</span><span>{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : 'Waiting for first sync'}</span></div>
        {!loading && filtered.length === 0 ? <div className="empty-state"><span className="empty-orbit">◎</span><h2>No notifications here yet</h2><p>{notifications.length ? 'Try another channel or search term.' : 'New Discord notifications will appear here automatically.'}</p></div> : filtered.map((notification) =>
          <article className={`notification-card ${CHANNEL_TONES[notification.channel] ?? 'default'}`} key={notification.id}>
            <div className="event-rail"><span className="event-dot" /></div>
            <div className="event-body"><div className="event-meta"><span className="channel-pill">{formatChannel(notification.channel)}</span><time dateTime={notification.created_at}>{formatTime(notification.created_at)}</time></div>
              {notification.content && <p className="event-content">{notification.content}</p>}{notification.description && <h2>{notification.description}</h2>}
              {notification.fields.length > 0 && <dl className="fields-grid">{notification.fields.map((field, index) => <div key={`${field.name}-${index}`}><dt>{field.name}</dt><dd>{String(field.value)}</dd></div>)}</dl>}
            </div>
          </article>)}
      </section>
    </main>
    <footer><span>ORBIT / SIGNAL OPERATIONS</span><span>Auto-refreshes every 15 seconds</span></footer>
  </div>
}

export default App
