// AP9 — "My paths": authenticated user's saved learning paths.
// AP11 — adds the daily-reminder opt-in toggle (no dedicated Settings page).

import React, { useEffect, useState } from 'react';
import { fetchMyPaths, setReminderOptIn } from '../services/auth';

export default function MyPathsPage({ user, onUserUpdate, onPick, onBack }) {
  const [paths, setPaths] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reminderBusy, setReminderBusy] = useState(false);
  const reminderOn = !!user?.reminder_opt_in;

  useEffect(() => {
    let cancelled = false;
    fetchMyPaths()
      .then((data) => { if (!cancelled) setPaths(Array.isArray(data) ? data : []); })
      .catch((err) => {
        if (cancelled) return;
        if (err.status === 401) setError('Please sign in to view your saved paths.');
        else setError(err.message || 'Could not load your paths.');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const toggleReminder = async () => {
    setReminderBusy(true);
    try {
      const updated = await setReminderOptIn(!reminderOn);
      if (onUserUpdate) onUserUpdate(updated);
    } catch { /* swallow — toggle stays in current state */ }
    finally { setReminderBusy(false); }
  };

  return (
    <div style={{ padding: '2rem', maxWidth: '900px', margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <h1>Your paths</h1>
        {onBack && <button className="btn btn-secondary" onClick={onBack}>← Back</button>}
      </div>

      {/* AP11 — reminder opt-in. Off by default; one click on/off. */}
      {user && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0.75rem 1rem', marginBottom: '1.5rem', borderRadius: '10px',
          background: reminderOn ? 'rgba(34, 197, 94, 0.08)' : 'rgba(255,255,255,0.04)',
          border: `1px solid ${reminderOn ? 'rgba(34, 197, 94, 0.3)' : 'rgba(255,255,255,0.08)'}`,
        }}>
          <div>
            <div style={{ fontSize: '0.95rem', fontWeight: 600 }}>
              {reminderOn ? '🔔 Daily reminders are on' : '🔕 Daily reminders are off'}
            </div>
            <div style={{ color: '#94a3b8', fontSize: '0.8rem', marginTop: '0.15rem' }}>
              We email you when a path's gone untouched for 3+ days. Unsubscribe link in every email.
            </div>
          </div>
          <button
            className="btn btn-secondary"
            disabled={reminderBusy}
            onClick={toggleReminder}
          >
            {reminderBusy ? '…' : reminderOn ? 'Turn off' : 'Turn on'}
          </button>
        </div>
      )}

      {loading && <p style={{ color: '#94a3b8' }}>Loading…</p>}
      {error && <p style={{ color: '#f87171' }}>{error}</p>}

      {!loading && !error && paths.length === 0 && (
        <p style={{ color: '#94a3b8' }}>
          No saved paths yet. Generate one from the home page — it'll save to your account automatically.
        </p>
      )}

      <div style={{ display: 'grid', gap: '1rem' }}>
        {paths.map((p) => {
          const completed = (p.milestones || []).filter((m) => m.completed).length;
          const total = (p.milestones || []).length;
          const pct = total ? Math.round((completed / total) * 100) : 0;
          return (
            <button
              key={p.id}
              onClick={() => onPick && onPick(p)}
              className="glass-card fade-in"
              style={{
                textAlign: 'left', padding: '1rem 1.25rem', borderRadius: '12px',
                cursor: 'pointer', border: '1px solid rgba(255,255,255,0.1)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem' }}>
                <h3 style={{ margin: 0 }}>{p.title}</h3>
                <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>
                  {pct}% · {completed}/{total}
                </span>
              </div>
              <p style={{ color: '#94a3b8', margin: '0.5rem 0 0', fontSize: '0.9rem' }}>
                {p.description}
              </p>
              <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.5rem', fontSize: '0.8rem', color: '#64748b' }}>
                <span>{p.experience_level}</span>
                <span>·</span>
                <span>{p.time_commitment}</span>
                {p.category && (<><span>·</span><span>{p.category}</span></>)}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
