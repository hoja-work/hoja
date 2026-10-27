import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {ChevronLeft, ChevronRight, Plus} from 'lucide-react';
import {fmtTime, meetingsApi, type Meeting, fmtDuration} from './api';
import './meetings-page.css';

type View = 'month' | 'week';
const HOUR_PX = 48;
const MAX_CHIPS = 3;

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const startOfWeek = (d: Date) => addDays(startOfDay(d), -((d.getDay() + 6) % 7)); // weeks start on Monday
const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
const sameDay = (a: Date, b: Date) => dayKey(a) === dayKey(b);

/** Scheduled meetings sit at their start; instant ones at when they began (or were created). */
function eventStart(m: Meeting): Date {
  const raw = m.scheduledStart || m.startedAt || (m as Meeting & {createdAt?: string}).createdAt;
  return raw ? new Date(raw) : new Date();
}

function eventClass(m: Meeting): string {
  const parts = ['mt-event'];
  if (m.status === 'live') parts.push('mt-ev-live');
  if (m.status === 'ended') parts.push('mt-ev-ended');
  if (m.isHost) parts.push('mt-ev-host');
  if (m.myResponse === 'declined') parts.push('mt-ev-declined');
  if (m.myResponse === 'needsAction' && !m.isHost) parts.push('mt-ev-pending');
  if (m.myResponse === 'tentative') parts.push('mt-ev-tentative');
  return parts.join(' ');
}

function eventLabel(m: Meeting): string {
  const when = m.scheduledStart ? fmtTime(m.scheduledStart) : m.status === 'live' ? 'live now' : 'instant';
  const state = m.status === 'ended' ? `, ended${m.durationSeconds ? `, lasted ${fmtDuration(m.durationSeconds)}` : ''}` : m.status === 'live' ? ', live' : m.isHost ? ', hosted by you' : m.myResponse && m.myResponse !== 'accepted' ? `, ${m.myResponse === 'needsAction' ? 'no reply yet' : m.myResponse}` : '';
  return `${m.title}, ${when}${state}`;
}

export function Calendar({version, onOpen, onCreateOn}: {
  version: number; onOpen: (m: Meeting) => void; onCreateOn?: (day: Date) => void;
}) {
  const [view, setView] = useState<View>('month');
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Date | null>(() => startOfDay(new Date()));
  const [now, setNow] = useState(() => new Date());
  const scroller = useRef<HTMLDivElement>(null);

  const days = useMemo(() => {
    const first = view === 'month' ? startOfWeek(new Date(anchor.getFullYear(), anchor.getMonth(), 1)) : startOfWeek(anchor);
    return Array.from({length: view === 'month' ? 42 : 7}, (_, i) => addDays(first, i));
  }, [view, anchor]);
  const from = days[0];
  const to = addDays(days[days.length - 1], 1);

  useEffect(() => {
    let live = true;
    setLoading(true);
    meetingsApi.calendar(from, to)
      .then((rows) => { if (live) { setMeetings(rows.filter((m) => m.status !== 'cancelled')); setError(''); } })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : 'Could not load the calendar.'); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from.getTime(), to.getTime(), version]);

  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 60000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    if (view === 'week' && scroller.current) scroller.current.scrollTop = Math.max(0, (new Date().getHours() - 1) * HOUR_PX);
  }, [view]);

  const byDay = useMemo(() => {
    const map = new Map<string, Meeting[]>();
    for (const m of meetings) {
      const k = dayKey(eventStart(m));
      map.set(k, [...(map.get(k) || []), m]);
    }
    for (const list of map.values()) list.sort((a, b) => eventStart(a).getTime() - eventStart(b).getTime());
    return map;
  }, [meetings]);

  const step = useCallback((dir: -1 | 1) => {
    setAnchor((a) => view === 'month' ? new Date(a.getFullYear(), a.getMonth() + dir, 1) : addDays(a, 7 * dir));
  }, [view]);

  const title = view === 'month'
    ? new Intl.DateTimeFormat(undefined, {month: 'long', year: 'numeric'}).format(anchor)
    : `${new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric'}).format(days[0])} – ${new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', year: 'numeric'}).format(days[6])}`;
  const weekdays = days.slice(0, 7).map((d) => new Intl.DateTimeFormat(undefined, {weekday: 'short'}).format(d));
  const today = startOfDay(now);
  const agenda = selected ? byDay.get(dayKey(selected)) || [] : [];

  return (
    <section className="mt-cal" aria-label="Meetings calendar">
      <header className="mt-cal-head">
        <div className="mt-cal-nav">
          <button type="button" className="mt-icon-btn" aria-label={view === 'month' ? 'Previous month' : 'Previous week'} onClick={() => step(-1)}><ChevronLeft size={16} /></button>
          <button type="button" className="mt-icon-btn" aria-label={view === 'month' ? 'Next month' : 'Next week'} onClick={() => step(1)}><ChevronRight size={16} /></button>
          <button type="button" className="mt-btn mt-btn-small" onClick={() => { setAnchor(startOfDay(new Date())); setSelected(startOfDay(new Date())); }}>Today</button>
        </div>
        <h2 className="mt-cal-title" aria-live="polite">{title}</h2>
        <div className="mt-segment" role="group" aria-label="Calendar view">
          <button type="button" aria-pressed={view === 'month'} className={view === 'month' ? 'mt-on' : ''} onClick={() => setView('month')}>Month</button>
          <button type="button" aria-pressed={view === 'week'} className={view === 'week' ? 'mt-on' : ''} onClick={() => setView('week')}>Week</button>
        </div>
      </header>
      {error && <p className="mt-field-error" role="alert">{error}</p>}

      {view === 'month' ? (
        <>
          <div className="mt-month" data-loading={loading || undefined}>
            {weekdays.map((w) => <div key={w} className="mt-weekday">{w}</div>)}
            {days.map((d) => {
              const list = byDay.get(dayKey(d)) || [];
              const outside = d.getMonth() !== anchor.getMonth();
              const cls = ['mt-day', outside ? 'mt-day-out' : '', sameDay(d, today) ? 'mt-day-today' : '', selected && sameDay(d, selected) ? 'mt-day-selected' : ''].filter(Boolean).join(' ');
              return (
                <div key={dayKey(d)} className={cls}>
                  <div className="mt-day-top">
                    <button type="button" className="mt-day-num" aria-pressed={!!selected && sameDay(d, selected)}
                      aria-label={`${new Intl.DateTimeFormat(undefined, {dateStyle: 'full'}).format(d)}, ${list.length} meeting${list.length === 1 ? '' : 's'}`}
                      onClick={() => setSelected(d)}>{d.getDate()}</button>
                    {onCreateOn && <button type="button" className="mt-day-add" aria-label={`Schedule a meeting on ${new Intl.DateTimeFormat(undefined, {dateStyle: 'long'}).format(d)}`} onClick={() => onCreateOn(d)}><Plus size={12} /></button>}
                  </div>
                  <ul className="mt-day-events">
                    {list.slice(0, MAX_CHIPS).map((m) => (
                      <li key={m.id}>
                        <button type="button" className={eventClass(m)} aria-label={eventLabel(m)} title={m.title} onClick={() => onOpen(m)}>
                          <i className="mt-dot" aria-hidden="true" />
                          <span className="mt-event-text"><time>{m.scheduledStart ? fmtTime(m.scheduledStart) : ''}</time> {m.title}</span>
                        </button>
                      </li>
                    ))}
                    {list.length > MAX_CHIPS && <li><button type="button" className="mt-more" onClick={() => setSelected(d)}>+{list.length - MAX_CHIPS} more</button></li>}
                  </ul>
                </div>
              );
            })}
          </div>
          <section className="mt-agenda" aria-label="Selected day">
            <h3>{selected ? new Intl.DateTimeFormat(undefined, {weekday: 'long', month: 'long', day: 'numeric'}).format(selected) : 'Pick a day'}</h3>
            {selected && agenda.length === 0 && <p className="mt-muted">Nothing scheduled.{onCreateOn ? ' ' : ''}{onCreateOn && <button type="button" className="mt-link-btn" onClick={() => onCreateOn(selected)}>Schedule a meeting</button>}</p>}
            <ul>
              {agenda.map((m) => (
                <li key={m.id}>
                  <button type="button" className={`${eventClass(m)} mt-agenda-item`} aria-label={eventLabel(m)} onClick={() => onOpen(m)}>
                    <i className="mt-dot" aria-hidden="true" />
                    <time>{m.scheduledStart ? fmtTime(m.scheduledStart) : m.status === 'live' ? 'Live' : 'Instant'}</time>
                    <span className="mt-event-title">{m.title}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </>
      ) : (
        <div className="mt-week" data-loading={loading || undefined}>
          <div className="mt-week-head">
            <div className="mt-gutter" />
            {days.map((d) => (
              <div key={dayKey(d)} className={`mt-week-day${sameDay(d, today) ? ' mt-day-today' : ''}`}>
                <small>{new Intl.DateTimeFormat(undefined, {weekday: 'short'}).format(d)}</small>
                <b>{d.getDate()}</b>
              </div>
            ))}
          </div>
          <div className="mt-week-scroll" ref={scroller}>
            <div className="mt-week-grid" style={{height: HOUR_PX * 24}}>
              <div className="mt-gutter">
                {Array.from({length: 24}, (_, h) => <span key={h} style={{top: h * HOUR_PX}}>{h === 0 ? '' : new Intl.DateTimeFormat(undefined, {hour: 'numeric'}).format(new Date(2000, 0, 1, h))}</span>)}
              </div>
              {days.map((d) => {
                const list = byDay.get(dayKey(d)) || [];
                const lanes: number[] = [];
                const placed = list.map((m) => {
                  const s = eventStart(m);
                  const startMin = s.getHours() * 60 + s.getMinutes();
                  const endMin = startMin + Math.max(m.durationMinutes || 60, 25);
                  let lane = lanes.findIndex((end) => end <= startMin);
                  if (lane < 0) { lane = lanes.length; lanes.push(endMin); } else lanes[lane] = endMin;
                  return {m, startMin, endMin, lane};
                });
                const count = Math.max(1, lanes.length);
                return (
                  <div key={dayKey(d)} className={`mt-week-col${sameDay(d, today) ? ' mt-day-today' : ''}`}>
                    {Array.from({length: 24}, (_, h) => <i key={h} className="mt-hour-line" style={{top: h * HOUR_PX}} />)}
                    {placed.map(({m, startMin, endMin, lane}) => (
                      <button key={m.id} type="button" className={`${eventClass(m)} mt-week-event`} aria-label={eventLabel(m)} title={m.title}
                        style={{top: (startMin / 60) * HOUR_PX, height: Math.min(((endMin - startMin) / 60) * HOUR_PX, HOUR_PX * 24 - (startMin / 60) * HOUR_PX) - 2, left: `${(lane / count) * 100}%`, width: `${100 / count}%`}}
                        onClick={() => onOpen(m)}>
                        <span className="mt-event-title">{m.title}</span>
                        <small>{m.scheduledStart ? fmtTime(m.scheduledStart) : 'Instant'}</small>
                      </button>
                    ))}
                    {sameDay(d, today) && <i className="mt-now-line" style={{top: ((now.getHours() * 60 + now.getMinutes()) / 60) * HOUR_PX}} aria-hidden="true" />}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
