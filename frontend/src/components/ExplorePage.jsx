import React, { useEffect, useState } from 'react';
import './LearningPath.css';
import './ExplorePage.css';

const API_BASE = 'http://localhost:8000/api';

const CATEGORY_ORDER = [
    'Programming',
    'Design',
    'Business',
    'Science',
    'Creative',
    'Language',
    'Other',
];

const CATEGORY_ICON = {
    Programming: '💻',
    Design: '🎨',
    Business: '💼',
    Science: '🔬',
    Creative: '✨',
    Language: '🗣️',
    Other: '📚',
};

export default function ExplorePage({ onBack }) {
    const [grouped, setGrouped] = useState({});
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const [openCategories, setOpenCategories] = useState({});

    useEffect(() => {
        let cancelled = false;
        fetch(`${API_BASE}/explore`)
            .then((r) => {
                if (!r.ok) throw new Error('Failed to load explore feed');
                return r.json();
            })
            .then((data) => {
                if (cancelled) return;
                setGrouped(data || {});
                // Open every category by default
                const openMap = {};
                Object.keys(data || {}).forEach((cat) => { openMap[cat] = true; });
                setOpenCategories(openMap);
            })
            .catch((e) => !cancelled && setError(e.message))
            .finally(() => !cancelled && setLoading(false));
        return () => { cancelled = true; };
    }, []);

    const toggleCategory = (cat) => {
        setOpenCategories((prev) => ({ ...prev, [cat]: !prev[cat] }));
    };

    const openShared = (id) => {
        window.location.href = `/share/${id}`;
    };

    const categoriesPresent = Object.keys(grouped);
    const sortedCategories = [
        ...CATEGORY_ORDER.filter((c) => categoriesPresent.includes(c)),
        ...categoriesPresent.filter((c) => !CATEGORY_ORDER.includes(c)),
    ];

    return (
        <div className="explore-page learning-path-container">
            <div className="explore-header glass-card fade-in">
                <button className="btn btn-secondary back-button" onClick={onBack}>
                    ← Back
                </button>
                <div className="path-header-content">
                    <h1 className="path-title">Explore Public Paths</h1>
                    <p className="path-description">
                        Discover learning paths shared by the community, grouped by category.
                    </p>
                </div>
            </div>

            {loading && (
                <div style={{ textAlign: 'center', padding: '4rem 1rem' }}>
                    <div className="spinner" style={{ margin: '0 auto' }}></div>
                    <p style={{ color: '#9ca3af', marginTop: '1rem' }}>Loading public paths…</p>
                </div>
            )}

            {error && (
                <div className="error-message" style={{ margin: '2rem 0' }}>
                    <span>⚠️ {error}</span>
                </div>
            )}

            {!loading && !error && sortedCategories.length === 0 && (
                <div className="glass-card fade-in" style={{ padding: '2rem', textAlign: 'center' }}>
                    <p>No public paths yet. Share one to seed the feed!</p>
                </div>
            )}

            {!loading && !error && sortedCategories.map((cat) => {
                const paths = grouped[cat] || [];
                if (!paths.length) return null;
                const isOpen = !!openCategories[cat];
                return (
                    <section key={cat} className="explore-category">
                        <button
                            type="button"
                            className="explore-category-header glass-card fade-in"
                            onClick={() => toggleCategory(cat)}
                            aria-expanded={isOpen}
                        >
                            <span className="explore-category-title">
                                <span className="explore-category-icon">{CATEGORY_ICON[cat] || '📚'}</span>
                                {cat}
                                <span className="explore-count">{paths.length}</span>
                            </span>
                            <span className="explore-chevron">{isOpen ? '▼' : '▶'}</span>
                        </button>

                        {isOpen && (
                            <div className="explore-grid">
                                {paths.map((p) => (
                                    <article
                                        key={p.id}
                                        className="explore-card glass-card fade-in"
                                        onClick={() => openShared(p.id)}
                                        onKeyDown={(e) => { if (e.key === 'Enter') openShared(p.id); }}
                                        role="button"
                                        tabIndex={0}
                                    >
                                        <h3 className="explore-card-title">{p.title}</h3>
                                        <p className="explore-card-desc">{p.description}</p>
                                        <div className="explore-card-meta">
                                            <span className="badge badge-primary">{p.experience_level}</span>
                                            <span className="meta-item">📋 {p.milestone_count} milestones</span>
                                            {/* AP7 — total hours on each card */}
                                            <span className="duration-badge">⏱ {p.total_hours}h</span>
                                        </div>
                                    </article>
                                ))}
                            </div>
                        )}
                    </section>
                );
            })}
        </div>
    );
}
