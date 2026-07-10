// AP30 — tier-grouped achievement badge grid, extracted from ProfilePage so
// the owner view (/profile) and the public view (/u/:id) render badges
// identically with no drift. Takes the /me/stats or /u/:id/stats payload.

import React from 'react';

export const TIER_ORDER = ['bronze', 'silver', 'gold'];
export const TIER_LABEL = { bronze: '🥉 Bronze', silver: '🥈 Silver', gold: '🥇 Gold' };
export const TIER_COLOR = {
    bronze: { earned: '#b45309', locked: '#7c5b2a' },
    silver: { earned: '#9ca3af', locked: '#64748b' },
    gold:   { earned: '#f59e0b', locked: '#78716c' },
};
export const METRIC_LABEL = {
    total_xp: 'XP',
    best_streak: 'day streak',
    completed_paths: 'paths completed',
};

const BadgeGrid = ({ stats }) => {
    const earnedSet = new Set(stats.earned_badges || []);
    const groupedByTier = TIER_ORDER.map((tier) => ({
        tier,
        badges: (stats.badges || []).filter((b) => b.tier === tier),
    }));

    return (
        <>
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
        </>
    );
};

export default BadgeGrid;
