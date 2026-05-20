import React, { useState, useEffect } from 'react';
import { forkPath, getPublicNotes } from '../services/auth';
import './LearningPath.css';

const API_BASE = 'http://localhost:8000/api';

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
                <h2 style={{ color: '#f87171' }}>Path not found</h2>
                <p style={{ color: '#9ca3af' }}>This learning path doesn't exist or isn't shared publicly.</p>
            </div>
        );
    }

    if (!path) {
        return (
            <div className="learning-path-container" style={{ textAlign: 'center', padding: '4rem 1rem' }}>
                <div className="spinner" style={{ margin: '0 auto' }}></div>
                <p style={{ color: '#9ca3af', marginTop: '1rem' }}>Loading shared path…</p>
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
                    <h1 className="path-title">{path.title}</h1>
                    {path.current_version && (
                        <div style={{ fontSize: '0.8rem', color: '#94a3b8', marginBottom: '0.25rem' }}>
                            🕰 {path.current_version}
                        </div>
                    )}
                    <p className="path-description">{path.description}</p>
                    {forkError && <p style={{ color: '#f87171', marginTop: '0.5rem' }}>{forkError}</p>}
                    <div className="path-meta">
                        <span className="badge badge-primary">{path.experience_level}</span>
                        <span className="meta-item">⏱️ {path.time_commitment}</span>
                        <span className="meta-item">📚 {totalHours} total hours</span>
                        <span className="meta-item">✅ {completedCount}/{totalCount} completed</span>
                    </div>
                    <div className="progress-section">
                        <div className="progress-bar">
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
                            <div className="milestone-header" onClick={() => setExpandedMilestone(expandedMilestone === milestone.id ? null : milestone.id)}>
                                <div className="milestone-number">
                                    {milestone.completed ? '✓' : index + 1}
                                </div>
                                <div className="milestone-info">
                                    <h3 className="milestone-title">{milestone.title}</h3>
                                    <div className="milestone-meta">
                                        <span className="milestone-hours">⏱️ {milestone.estimated_hours}h</span>
                                        {milestone.completed && <span className="badge badge-success">Completed</span>}
                                    </div>
                                </div>
                                <button className="expand-button">
                                    {expandedMilestone === milestone.id ? '▼' : '▶'}
                                </button>
                            </div>

                            {expandedMilestone === milestone.id && (
                                <div className="milestone-details">
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
