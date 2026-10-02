import React, { useState, useEffect } from 'react';
import { forkPath, getPublicNotes } from '../services/auth';
import { downloadIcs } from '../utils/exportMarkdown';
import './LearningPath.css';
import LiveAlert from './LiveAlert';

// AP31 — relative by default, so the SPA and the API share an origin in
// production and there is no build-time URL to get wrong. Local dev is
// unchanged: vite.config.js already proxies /api to localhost:8000.
const API_BASE = import.meta.env.VITE_API_BASE || '/api';

function parseResource(r) {
    try {
        const obj = typeof r === 'string' ? JSON.parse(r) : r;
        if (obj && obj.url) return obj;
    } catch { /* plain string */ }
    return null;
}

const TYPE_ICON = { video: '🎬', docs: '📖', article: '📰' };

export default function SharePathPage({ pathId, user, onSignIn, onForked }) {
    const [path, setPath] = useState(null);
    const [error, setError] = useState(null);
    const [expandedMilestone, setExpandedMilestone] = useState(null);
    const [forking, setForking] = useState(false);
    const [forkError, setForkError] = useState('');
    // AP40 — calendar export failures used to go to console.warn only.
    const [exportError, setExportError] = useState('');
    const [publicNotes, setPublicNotes] = useState({});  // AP12 — { milestone_id: [{content, author, updated_at}] }

    useEffect(() => {
        fetch(`${API_BASE}/paths/${pathId}/public`)
            .then((r) => {
                if (!r.ok) throw new Error('Path not found or not public');
                return r.json();
            })
            .then(setPath)
            .catch((e) => setError(e.message));
        // AP12 — fire-and-forget; failure is OK (notes simply don't render)
        getPublicNotes(pathId).then(setPublicNotes).catch(() => {});
    }, [pathId]);

    const handleFork = async () => {
        if (!user) { onSignIn && onSignIn(); return; }
        setForking(true);
        setForkError('');
        try {
            const newPath = await forkPath(pathId);
            if (onForked) onForked(newPath);
        } catch (err) {
            setForkError(err.message || 'Could not fork this path.');
        } finally {
            setForking(false);
        }
    };

    if (error) {
        return (
            <div className="learning-path-container" style={{ textAlign: 'center', padding: '4rem 1rem' }}>
                <div role="alert">
                    <h2 style={{ color: '#f87171' }}>Path not found</h2>
                    <p style={{ color: '#9ca3af' }}>This learning path doesn't exist or isn't shared publicly.</p>
                </div>
            </div>
        );
    }

    if (!path) {
        return (
            <div className="learning-path-container" style={{ textAlign: 'center', padding: '4rem 1rem' }} aria-busy="true">
                <div className="spinner" style={{ margin: '0 auto' }} aria-hidden="true"></div>
                <p role="status" style={{ color: '#9ca3af', marginTop: '1rem' }}>Loading shared path…</p>
            </div>
        );
    }

    const completedCount = path.milestones.filter((m) => m.completed).length;
    const totalCount = path.milestones.length;
    const progressPct = totalCount ? (completedCount / totalCount) * 100 : 0;
    const totalHours = path.milestones.reduce((s, m) => s + m.estimated_hours, 0);

    return (
        <div className="learning-path-container">
            <div className="path-header glass-card fade-in">
                <div className="path-header-content">
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', marginBottom: '0.5rem' }}>
                        <span className="badge badge-primary">Shared Path</span>
                        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                            {/* AP25 — calendar export (also available on public paths) */}
                            <button
                                className="btn btn-secondary"
                                onClick={async () => {
                                    setExportError('');
                                    try { await downloadIcs(API_BASE, path.id); }
                                    catch (err) { console.warn('Calendar export failed', err); setExportError('Calendar export failed. Please try again.'); }
                                }}
                                title="Download an .ics calendar file with milestone reminders"
                                style={{ whiteSpace: 'nowrap' }}
                            >
                                📅 Add to calendar
                            </button>
                            {/* AP29 — same calendar plus a recurring weekly study block */}
                            <button
                                className="btn btn-secondary"
                                onClick={async () => {
                                    setExportError('');
                                    try { await downloadIcs(API_BASE, path.id, { studyBlocks: true }); }
                                    catch (err) { console.warn('Calendar export failed', err); setExportError('Calendar export failed. Please try again.'); }
                                }}
                                title="Download the calendar with a recurring weekly study block"
                                style={{ whiteSpace: 'nowrap' }}
                            >
                                📅 + study blocks
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleFork}
                                disabled={forking}
                                title={user ? 'Make your own editable copy' : 'Sign in to fork'}
                                style={{ whiteSpace: 'nowrap' }}
                            >
                                {forking ? 'Forking…' : (user ? '🍴 Fork this path' : 'Sign in to fork')}
                            </button>
                        </div>
                    </div>
                    <h1 className="path-title">{path.title}</h1>
                    {path.current_version && (
                        <div style={{ fontSize: '0.8rem', color: '#94a3b8', marginBottom: '0.25rem' }}>
                            🕰 {path.current_version}
                        </div>
                    )}
                    <p className="path-description">{path.description}</p>
                    <LiveAlert message={forkError || exportError} className={null} icon="" style={{ color: '#f87171', marginTop: '0.5rem' }} />
                    <div className="path-meta">
                        <span className="badge badge-primary">{path.experience_level}</span>
                        <span className="meta-item">⏱️ {path.time_commitment}</span>
                        <span className="meta-item">📚 {totalHours} total hours</span>
                        <span className="meta-item">✅ {completedCount}/{totalCount} completed</span>
                    </div>
                    <div className="progress-section">
                        <div
                            className="progress-bar"
                            role="progressbar"
                            aria-label="Path progress"
                            aria-valuemin={0}
                            aria-valuemax={100}
                            aria-valuenow={Math.round(progressPct)}
                        >
                            <div className="progress-fill" style={{ width: `${progressPct}%` }}></div>
                        </div>
                        <span className="progress-text">{Math.round(progressPct)}% Complete</span>
                    </div>
                </div>
            </div>

            <div className="milestones-container">
                <h2 className="milestones-title fade-in">Learning Journey</h2>
                <div className="milestones-timeline">
                    {path.milestones.map((milestone, index) => (
                        <div
                            key={milestone.id}
                            className={`milestone-card glass-card fade-in ${milestone.completed ? 'completed' : ''}`}
                            style={{ animationDelay: `${index * 0.1}s` }}
                        >
                            {/* AP40 — was a <div onClick>, unreachable by keyboard; the title
                                is now the disclosure button (same as LearningPath). */}
                            <div className="milestone-header">
                                <div className="milestone-number" aria-hidden="true">
                                    {milestone.completed ? '✓' : index + 1}
                                </div>
                                <div className="milestone-info">
                                    <h3 className="milestone-title">
                                        <button
                                            type="button"
                                            className="milestone-toggle"
                                            aria-expanded={expandedMilestone === milestone.id}
                                            aria-controls={`milestone-details-${milestone.id}`}
                                            onClick={() => setExpandedMilestone(expandedMilestone === milestone.id ? null : milestone.id)}
                                        >
                                            <span className="sr-only">Milestone {index + 1}: </span>
                                            {milestone.title}
                                        </button>
                                    </h3>
                                    <div className="milestone-meta">
                                        <span className="milestone-hours">⏱️ {milestone.estimated_hours}h</span>
                                        {milestone.completed && <span className="badge badge-success">Completed</span>}
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    className="expand-button"
                                    tabIndex={-1}
                                    aria-hidden="true"
                                    onClick={() => setExpandedMilestone(expandedMilestone === milestone.id ? null : milestone.id)}
                                >
                                    {expandedMilestone === milestone.id ? '▼' : '▶'}
                                </button>
                            </div>

                            {expandedMilestone === milestone.id && (
                                <div className="milestone-details" id={`milestone-details-${milestone.id}`}>
                                    <div className="milestone-description">
                                        <h4>What You'll Learn</h4>
                                        <p>{milestone.description}</p>
                                    </div>
                                    <div className="milestone-resources">
                                        <h4>Resources</h4>
                                        <div className="resource-list">
                                            {(milestone.resources || []).map((r, idx) => {
                                                const obj = parseResource(r);
                                                if (obj) {
                                                    return (
                                                        <a key={idx} href={obj.url} target="_blank" rel="noopener noreferrer" className="resource-chip">
                                                            <span className="resource-type">{TYPE_ICON[obj.type] || '📖'}</span>
                                                            <span>{obj.title}</span>
                                                        </a>
                                                    );
                                                }
                                                return (
                                                    <span key={idx} className="resource-chip resource-chip--plain">
                                                        <span className="resource-type">📖</span>
                                                        <span>{r}</span>
                                                    </span>
                                                );
                                            })}
                                        </div>
                                    </div>

                                    {/* AP12 — public reflections from forkers / followers */}
                                    {(publicNotes[milestone.id] || publicNotes[String(milestone.id)] || []).length > 0 && (
                                        <div className="milestone-reflections" style={{ marginTop: '1rem' }}>
                                            <h4>Reflections from learners</h4>
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                                                {(publicNotes[milestone.id] || publicNotes[String(milestone.id)] || []).map((n, i) => (
                                                    <div key={i} style={{
                                                        background: 'rgba(255,255,255,0.04)',
                                                        border: '1px solid rgba(255,255,255,0.08)',
                                                        borderRadius: '8px',
                                                        padding: '0.6rem 0.8rem',
                                                    }}>
                                                        <p style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: '0.9rem' }}>{n.content}</p>
                                                        <p style={{ margin: '0.25rem 0 0', fontSize: '0.75rem', color: '#64748b' }}>
                                                            — {n.author}
                                                        </p>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}

                            {index < path.milestones.length - 1 && <div className="timeline-connector"></div>}
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
