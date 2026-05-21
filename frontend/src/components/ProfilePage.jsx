// AP26 — Achievement badges + aggregate stats.
// Badge definitions come from the API (single source of truth — no drift);
// earned set is computed server-side. Client groups by tier and renders
// earned (colour) vs locked (grey + threshold hint).

import React, { useEffect, useState } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:8000/api';

const TIER_ORDER = ['bronze', 'silver', 'gold'];
const TIER_LABEL = { bronze: '🥉 Bronze', silver: '🥈 Silver', gold: '🥇 Gold' };
const TIER_COLOR = {
    bronze: { earned: '#b45309', locked: '#7c5b2a' },
    silver: { earned: '#9ca3af', locked: '#64748b' },
    gold:   { earned: '#f59e0b', locked: '#78716c' },
};

const METRIC_LABEL = {
    total_xp: 'XP',
    best_streak: 'day streak',
    completed_paths: 'paths completed',
};

const ProfilePage = ({ user, onBack }) => {
    const [stats, setStats] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
        axios
            .get(`${API_BASE}/me/stats`, { withCredentials: true })
            .then((res) => setStats(res.data))
            .catch((err) => {
                console.warn('Failed to load /me/stats', err);
                setError('Could not load your stats. Are you signed in?');
            });
    }, []);

    if (error) {
        return (
            <div className="learning-path-container">
                <div className="glass-card fade-in" style={{ padding: '2rem', textAlign: 'center' }}>
                    <p style={{ color: '#f87171' }}>{error}</p>
                    <button className="btn btn-secondary" onClick={onBack} style={{ marginTop: '1rem' }}>← Back</button>
                </div>
            </div>
        );
    }

    if (!stats) {
        return (
            <div className="learning-path-container">
                <div className="glass-card fade-in" style={{ padding: '2rem', textAlign: 'center' }}>
                    Loading…
                </div>
            </div>
        );
    }

    const earnedSet = new Set(stats.earned_badges || []);
    const groupedByTier = TIER_ORDER.map((tier) => ({
        tier,
        badges: (stats.badges || []).filter((b) => b.tier === tier),
    }));

    return (
        <div className="learning-path-container">
            <div className="path-header glass-card fade-in">
                <button className="btn btn-secondary back-button" onClick={onBack}>
                    ← Back
                </button>
                <div className="path-header-content">
                    <h1 className="path-title">Profile</h1>
                    {user?.email && (
                        <p className="path-description" style={{ color: '#94a3b8' }}>{user.email}</p>
                    )}
                    <div className="path-meta" style={{ marginTop: '0.5rem' }}>
                        <span className="xp-badge">⭐ {stats.total_xp} XP</span>
                        {stats.best_streak > 0 && (
                            <span className="streak-badge">🔥 Best {stats.best_streak} day streak</span>
                        )}
                        <span className="meta-item">📚 {stats.completed_paths}/{stats.total_paths} paths completed</span>
                    </div>
                </div>
            </div>

            <div className="milestones-container">
                <h2 className="milestones-title fade-in">Achievements</h2>
                {groupedByTier.map(({ tier, badges }) => (
                    <div key={tier} style={{ marginBottom: '1.5rem' }}>
                        <h3 style={{ fontSize: '1.1rem', color: '#cbd5e1', marginBottom: '0.6rem' }}>
                            {TIER_LABEL[tier]}
                        </h3>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.6rem' }}>
                            {badges.map((b) => {
                                const earned = earnedSet.has(b.id);
                                const accent = earned ? TIER_COLOR[tier].earned : TIER_COLOR[tier].locked;
                                const progress = stats[b.metric] ?? 0;
                                const metricLabel = METRIC_LABEL[b.metric] || b.metric;
                                return (
                                    <div
                                        key={b.id}
                                        title={earned ? 'Earned' : `Reach ${b.threshold} ${metricLabel} (currently ${progress})`}
                                        style={{
                                            padding: '0.7rem 0.9rem',
                                            background: earned ? `${accent}33` : 'rgba(30, 41, 59, 0.4)',
                                            border: `1px solid ${accent}`,
                                            borderRadius: '0.5rem',
                                            minWidth: '150px',
                                            opacity: earned ? 1 : 0.55,
                                        }}
                                    >
                                        <div style={{ fontWeight: 600, fontSize: '0.95rem', color: earned ? '#e2e8f0' : '#94a3b8' }}>
                                            {b.label}
                                        </div>
                                        <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginTop: '0.2rem' }}>
                                            {earned ? 'Earned' : `${progress} / ${b.threshold} ${metricLabel}`}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default ProfilePage;
