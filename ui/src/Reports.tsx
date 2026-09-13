import { useCallback, useEffect, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, FileText, Moon, RefreshCw, Sun } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { DAY_MS, istDateValue as dateValue, istMidnight, previousIstDay, previousCompletedWeek, containingSaturday, formatIst } from "./reportCalendar";

type ReportKind = "daily" | "weekly";
type Theme = "light" | "dark";
type Report = { report_type: ReportKind; period_start: string; period_end: string; timezone: string; body: string; metrics: { net_pnl: number; equity_value: number; opening_equity: number; equity_change_pct: number | null; max_drawdown: number; max_drawdown_pct: number | null; realized_pnl: number; commission: number; funding: number; other_income: number } };

const initialTheme = (): Theme => { const stored = localStorage.getItem("orbit-theme"); return stored === "light" || stored === "dark" ? stored : "dark"; };

export function ReportPage({ kind }: { kind: ReportKind }) {
  const [selected, setSelected] = useState(() => dateValue(kind === "daily" ? previousIstDay() : previousCompletedWeek()));
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const requestGeneration = useRef(0);
  const step = kind === "daily" ? 1 : 7;

  const load = useCallback(async () => { const generation = ++requestGeneration.current; setLoading(true); setReport(null); try { const query = kind === "daily" ? `report_date=${selected}` : `week_start=${selected}`; const response = await fetch(`/api/reports/${kind}?${query}`); if (!response.ok) throw new Error(`Report request failed (${response.status}).`); const nextReport = await response.json() as Report; if (generation !== requestGeneration.current) return; setReport(nextReport); setError(""); } catch (requestError) { if (generation !== requestGeneration.current) return; setError(requestError instanceof Error ? requestError.message : "Report is unavailable."); } finally { if (generation === requestGeneration.current) setLoading(false); } }, [kind, selected]);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("orbit-theme", theme); }, [theme]);
  useEffect(() => { void load(); }, [load]);

  const move = (direction: number) => setSelected(dateValue(new Date(istMidnight(selected).getTime() + direction * step * DAY_MS)));
  const latest = dateValue(kind === "daily" ? previousIstDay() : previousCompletedWeek());
  const title = kind === "daily" ? "Daily Testnet report" : "Weekly Testnet report";

  return <div className="report-shell">
    <header className="report-topbar">
      <a className="brand" href="/"><span className="brand-symbol"><i /><i /><i /></span><span>ORBIT</span></a>
      <nav><a href="/">Command center</a><a className={kind === "daily" ? "active" : ""} href="/daily">Daily</a><a className={kind === "weekly" ? "active" : ""} href="/weekly">Weekly</a></nav>
      <button className="icon-button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</button>
    </header>
    <main className="report-page">
      <section className="report-heading"><div><small>TESTNET EVIDENCE · IST</small><h1>{title}</h1><p>{kind === "daily" ? "A complete midnight-to-midnight IST day." : "A completed Saturday 00:00 through Saturday 00:00 IST window (Saturday–Friday)."}</p></div><div className="report-controls"><button onClick={() => move(-1)} aria-label={`Previous ${kind === "daily" ? "day" : "week"}`}><ChevronLeft size={16} /></button><label><CalendarDays size={14} /><input type="date" value={selected} max={latest} onChange={(event) => { if (event.target.value) setSelected(kind === "weekly" ? containingSaturday(event.target.value) : event.target.value); }} /></label><button onClick={() => move(1)} disabled={selected >= latest} aria-label={`Next ${kind === "daily" ? "day" : "week"}`}><ChevronRight size={16} /></button><button onClick={() => void load()} aria-label="Refresh report"><RefreshCw className={loading ? "spinning" : ""} size={16} /></button></div></section>
      {error && <div className="error-banner"><strong>Report unavailable</strong><span>{error}</span></div>}
      {report && <section className="performance-cards" aria-label="Account performance">{([ ["Net P&L", report.metrics.net_pnl, "USDT"], ["Equity value", report.metrics.equity_value, "USDT"], ["Equity change", report.metrics.equity_change_pct, "%"], ["Max drawdown", report.metrics.max_drawdown, "USDT"] ] as [string, number | null, string][]).map(([label, value, unit]) => <div key={label}><span>{label}</span><strong>{value == null ? "Undefined (non-positive opening equity)" : value.toLocaleString(undefined, { maximumFractionDigits: 8 })} {value == null ? "" : unit}</strong></div>)}</section>}
      <article className="report-document">{loading && !report ? <div className="report-loading"><RefreshCw className="spinning" /><span>Building report from the immutable ledgers…</span></div> : report ? <><div className="report-period"><FileText size={16} /><span>{formatIst(report.period_start)} to {formatIst(report.period_end)} {report.timezone} (end exclusive)</span></div><div className="markdown-report"><ReactMarkdown remarkPlugins={[remarkGfm]}>{report.body}</ReactMarkdown></div></> : null}</article>
    </main>
  </div>;
}
