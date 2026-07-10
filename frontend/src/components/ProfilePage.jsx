// AP26 — Achievement badges + aggregate stats.
// Badge definitions come from the API (single source of truth — no drift);
// earned set is computed server-side. Client groups by tier and renders
// earned (colour) vs locked (grey + threshold hint).

import React, { useEffect, useState } from 'react';
import axios from 'axios';
import BadgeGrid from './BadgeGrid';

const API_BASE = 'http://localhost:8000/api';

const ProfilePage = ({ user, onBack }) => {
    const [stats, setStats] = useState(null);
    const [error, setError] = useState(null);
    // AP30 — public profile opt-in
    const [isPublic, setIsPublic] = useState(false);
    const [copied, setCopied] = useState(false);

    useEffect(() => {
        axios
            .get(`${API_BASE}/me/stats`, { withCredentials: true })
            .then((res) => setStats(res.data))
            .catch((err) => {
                console.warn('Failed to load /me/stats', err);
                setError('Could not load your stats. Are you signed in?');
            });
    }, []);

    // AP30 — seed the toggle from the signed-in user record when available.
    useEffect(() => {
        if (user && typeof user.is_public_profile === 'boolean') {
            setIsPublic(user.is_public_profile);
        }
    }, [user]);

    const publicUrl = user ? `${window.location.origin}/u/${user.id}` : '';

    const handleTogglePublic = async () => {
        const next = !isPublic;
        try {
            const res = await axios.patch(
                `${API_BASE}/me/profile/visibility`,
                { is_public_profile: next },
                { withCredentials: true },
            );
            setIsPublic(res.data.is_public_profile);
        } catch (err) {
            console.warn('Failed to update profile visibility', err);
        }
    };

    const handleCopyLink = async () => {
        try {
            await navigator.clipboard.writeText(publicUrl);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch {
            /* clipboard unavailable — no-op */
        }
    };

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

                    {/* AP30 — public profile opt-in + shareable link */}
                    {user && (
                        <div style={{ marginTop: '1rem', paddingTop: '0.75rem', borderTop: '1px solid rgba(148,163,184,0.2)' }}>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontSize: '0.9rem', color: '#cbd5e1' }}>
                                <input type="checkbox" checked={isPublic} onChange={handleTogglePublic} />
                                Make my profile public
                            </label>
                            {isPublic && (
                                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
                                    <code style={{ fontSize: '0.8rem', color: '#94a3b8', background: 'rgba(30,41,59,0.5)', padding: '0.3rem 0.5rem', borderRadius: '0.35rem' }}>
                                        {publicUrl}
                                    </code>
                                    <button className="btn btn-secondary" onClick={handleCopyLink} style={{ padding: '0.3rem 0.7rem', fontSize: '0.8rem' }}>
                                        {copied ? '✓ Copied!' : '🔗 Copy link'}
                                    </button>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>

            <div className="milestones-container">
                <h2 className="milestones-title fade-in">Achievements</h2>
                <BadgeGrid stats={stats} />
            </div>
        </div>
    );
};

export default ProfilePage;
