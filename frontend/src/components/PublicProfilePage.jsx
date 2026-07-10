// AP30 — public, unauthenticated profile page at /u/:id. Fetches the public
// stats endpoint WITHOUT credentials, reuses the shared BadgeGrid, and shows
// a clear "private or not found" state on 404.

import React, { useEffect, useState } from 'react';
import axios from 'axios';
import BadgeGrid from './BadgeGrid';

const API_BASE = 'http://localhost:8000/api';

const PublicProfilePage = ({ userId, onBack }) => {
    const [stats, setStats] = useState(null);
    const [notFound, setNotFound] = useState(false);

    useEffect(() => {
        // No credentials — this is a public read.
        axios
            .get(`${API_BASE}/u/${userId}/stats`)
            .then((res) => setStats(res.data))
            .catch(() => setNotFound(true));
    }, [userId]);

    if (notFound) {
        return (
            <div className="learning-path-container">
                <div className="glass-card fade-in" style={{ padding: '2rem', textAlign: 'center' }}>
                    <h2>Profile unavailable</h2>
                    <p style={{ color: '#94a3b8' }}>
                        This profile is private or doesn’t exist.
                    </p>
                    <button className="btn btn-secondary" onClick={onBack} style={{ marginTop: '1rem' }}>
                        ← Home
                    </button>
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
                    ← Home
                </button>
                <div className="path-header-content">
                    <h1 className="path-title">Learner Profile</h1>
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
                <BadgeGrid stats={stats} />
            </div>
        </div>
    );
};

export default PublicProfilePage;
