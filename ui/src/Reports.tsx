import { useCallback, useEffect, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, FileText, Moon, RefreshCw, Sun } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type ReportKind = "daily" | "weekly";
type Theme = "light" | "dark";
type Report = { report_type: ReportKind; period_start: string; period_end: string; timezone: string; body: string };

const DAY_MS = 86_400_000;
const dateValue = (value: Date) => value.toISOString().slice(0, 10);
const utcDate = (value: string) => new Date(`${value}T00:00:00Z`);
const initialTheme = (): Theme => { const stored = localStorage.getItem("orbit-theme"); return stored === "light" || stored === "dark" ? stored : "dark"; };
const previousUtcDay = () => new Date(Date.now() - DAY_MS);
const previousCompletedWeek = () => { const today = new Date(); const utcDay = today.getUTCDay(); const daysSinceSaturday = (utcDay + 1) % 7; const latestSaturday = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - daysSinceSaturday)); return new Date(latestSaturday.getTime() - 7 * DAY_MS); };
const containingSaturday = (value: string) => { const selected = utcDate(value); return dateValue(new Date(selected.getTime() - ((selected.getUTCDay() + 1) % 7) * DAY_MS)); };

export function ReportPage({ kind }: { kind: ReportKind }) {
  const [selected, setSelected] = useState(() => dateValue(kind === "daily" ? previousUtcDay() : previousCompletedWeek()));
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const step = kind === "daily" ? 1 : 7;

  const load = useCallback(async () => { setLoading(true); setReport(null); try { const query = kind === "daily" ? `report_date=${selected}` : `week_start=${selected}`; const response = await fetch(`/api/reports/${kind}?${query}`); if (!response.ok) throw new Error(`Report request failed (${response.status}).`); setReport(await response.json() as Report); setError(""); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Report is unavailable."); } finally { setLoading(false); } }, [kind, selected]);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("orbit-theme", theme); }, [theme]);
  useEffect(() => { void load(); }, [load]);

  const move = (direction: number) => setSelected(dateValue(new Date(utcDate(selected).getTime() + direction * step * DAY_MS)));
  const latest = dateValue(kind === "daily" ? previousUtcDay() : previousCompletedWeek());
  const title = kind === "daily" ? "Daily Testnet report" : "Weekly Testnet report";

  return <div className="report-shell">
    <header className="report-topbar">
      <a className="brand" href="/"><span className="brand-symbol"><i /><i /><i /></span><span>ORBIT</span></a>
      <nav><a href="/">Command center</a><a className={kind === "daily" ? "active" : ""} href="/daily">Daily</a><a className={kind === "weekly" ? "active" : ""} href="/weekly">Weekly</a></nav>
      <button className="icon-button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</button>
    </header>
    <main className="report-page">
      <section className="report-heading"><div><small>TESTNET EVIDENCE · UTC</small><h1>{title}</h1><p>{kind === "daily" ? "A complete midnight-to-midnight UTC day." : "A completed Saturday 00:00 through Saturday 00:00 UTC window (Saturday–Friday)."}</p></div><div className="report-controls"><button onClick={() => move(-1)} aria-label={`Previous ${kind === "daily" ? "day" : "week"}`}><ChevronLeft size={16} /></button><label><CalendarDays size={14} /><input type="date" value={selected} max={latest} onChange={(event) => setSelected(kind === "weekly" ? containingSaturday(event.target.value) : event.target.value)} /></label><button onClick={() => move(1)} disabled={selected >= latest} aria-label={`Next ${kind === "daily" ? "day" : "week"}`}><ChevronRight size={16} /></button><button onClick={() => void load()} aria-label="Refresh report"><RefreshCw className={loading ? "spinning" : ""} size={16} /></button></div></section>
      {error && <div className="error-banner"><strong>Report unavailable</strong><span>{error}</span></div>}
      <article className="report-document">{loading && !report ? <div className="report-loading"><RefreshCw className="spinning" /><span>Building report from the immutable ledgers…</span></div> : report ? <><div className="report-period"><FileText size={16} /><span>{new Date(report.period_start).toISOString().replace("T", " ").slice(0, 19)} to {new Date(report.period_end).toISOString().replace("T", " ").slice(0, 19)} {report.timezone}</span></div><div className="markdown-report"><ReactMarkdown remarkPlugins={[remarkGfm]}>{report.body}</ReactMarkdown></div></> : null}</article>
    </main>
  </div>;
}
