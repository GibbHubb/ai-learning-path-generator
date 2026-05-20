import React, { useState } from 'react';
import axios from 'axios';
import { downloadMarkdown } from '../utils/exportMarkdown';
import MilestoneNotes from './MilestoneNotes';
import QuizModal from './QuizModal';
import './LearningPath.css';

const API_BASE = 'http://localhost:8000/api';

function parseResource(r) {
    try {
        const obj = typeof r === 'string' ? JSON.parse(r) : r;
        if (obj && obj.url) return obj; // {title, url, type}
    } catch { /* not JSON — plain string */ }
    return null;
}

const TYPE_ICON = { video: '🎬', docs: '📖', article: '📰' };

const LearningPath = ({ pathData, onBack, onRefresh, user, onSignIn }) => {
    const [milestones, setMilestones] = useState(pathData.milestones || []);
    const [expandedMilestone, setExpandedMilestone] = useState(null);
    const [totalXp, setTotalXp] = useState(pathData.total_xp || 0);
    const [streakDays, setStreakDays] = useState(pathData.streak_days || 0);
    const [copied, setCopied] = useState(false);
    const [isPublic, setIsPublic] = useState(pathData.is_public || false);
    // AP5 — which milestone id is currently showing its feedback panel
    const [feedbackPromptId, setFeedbackPromptId] = useState(null);
    const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
    const [pathAdjustedFlash, setPathAdjustedFlash] = useState(false);
    // AP8 — when set, the QuizModal renders for this milestone id
    const [quizForMilestoneId, setQuizForMilestoneId] = useState(null);

    // AP8 — local-state patch when a quiz attempt passes (called from QuizModal).
    // The server has already marked the milestone complete + recomputed XP/streak.
    const handleQuizPassed = (result) => {
        if (!quizForMilestoneId) return;
        setMilestones((prev) => prev.map((m) =>
            m.id === quizForMilestoneId ? { ...m, completed: true } : m
        ));
        if (typeof result.total_xp === 'number') setTotalXp(result.total_xp);
        if (typeof result.streak_days === 'number') setStreakDays(result.streak_days);
        // AP5 — surface the feedback prompt the same way toggleComplete does
        setFeedbackPromptId(quizForMilestoneId);
    };

    const toggleMilestone = (milestoneId) => {
        setExpandedMilestone(expandedMilestone === milestoneId ? null : milestoneId);
    };

    const toggleComplete = async (milestone) => {
        try {
            const wasCompleted = milestone.completed;
            const res = await axios.patch(`${API_BASE}/milestones/${milestone.id}`, {
                completed: !wasCompleted
            });

            setMilestones(milestones.map(m =>
                m.id === milestone.id
                    ? { ...m, completed: !m.completed }
                    : m
            ));

            // AP4 — update XP + streak from response
            if (res.data.total_xp !== undefined) setTotalXp(res.data.total_xp);
            if (res.data.streak_days !== undefined) setStreakDays(res.data.streak_days);

            // AP5 — if the milestone was just marked complete (not unchecked), ask for feedback
            if (!wasCompleted) {
                setFeedbackPromptId(milestone.id);
            } else {
                setFeedbackPromptId((current) => (current === milestone.id ? null : current));
            }
        } catch (error) {
            console.error('Error updating milestone:', error);
        }
    };

    // AP5 — submit difficulty feedback, optionally replace remaining milestones
    const submitFeedback = async (milestone, feedback) => {
        if (feedbackSubmitting) return;
        setFeedbackSubmitting(true);
        try {
            const res = await axios.post(
                `${API_BASE}/milestones/${milestone.id}/feedback`,
                { milestone_id: milestone.id, feedback }
            );

            if (feedback === 'too_easy' || feedback === 'too_hard') {
                if (res.data && Array.isArray(res.data.milestones)) {
                    setMilestones(res.data.milestones);
                    setPathAdjustedFlash(true);
                    setTimeout(() => setPathAdjustedFlash(false), 2500);
                }
            }
            setFeedbackPromptId(null);
        } catch (err) {
            console.error('Feedback failed:', err);
        } finally {
            setFeedbackSubmitting(false);
        }
    };

    const handleShare = async () => {
        try {
            if (!isPublic) {
                await axios.patch(`${API_BASE}/paths/${pathData.id}/share`, { is_public: true });
                setIsPublic(true);
            }
            const url = `${window.location.origin}/share/${pathData.id}`;
            await navigator.clipboard.writeText(url);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch (err) {
            console.error('Share failed:', err);
        }
    };

    const handleExport = () => {
        downloadMarkdown({ ...pathData, milestones });
    };

    const completedCount = milestones.filter(m => m.completed).length;
    const totalCount = milestones.length;
    const progressPercentage = totalCount ? (completedCount / totalCount) * 100 : 0;
    const totalHours = milestones.reduce((sum, m) => sum + m.estimated_hours, 0);

    return (
        <div className="learning-path-container">
            {/* AP8 — quiz modal portal */}
            {quizForMilestoneId && (
                <QuizModal
                    milestoneId={quizForMilestoneId}
                    onClose={() => setQuizForMilestoneId(null)}
                    onPassed={handleQuizPassed}
                />
            )}
            {pathAdjustedFlash && (
                <div className="path-adjusted-flash glass-card fade-in">
                    ✨ Path adjusted!
                </div>
            )}
            <div className="path-header glass-card fade-in">
                <button className="btn btn-secondary back-button" onClick={onBack}>
                    ← Back
                </button>

                <div className="path-header-content">
                    <h1 className="path-title">{pathData.title}</h1>
                    <p className="path-description">{pathData.description}</p>

                    <div className="path-meta">
                        <span className="badge badge-primary">{pathData.experience_level}</span>
                        <span className="meta-item">⏱️ {pathData.time_commitment}</span>
                        <span className="meta-item">📚 {totalHours} total hours</span>
                        <span className="meta-item">
                            ✅ {completedCount}/{totalCount} completed
                        </span>
                        {/* AP4 — XP + streak badges */}
                        <span className="xp-badge">⭐ {totalXp} XP</span>
                        {streakDays > 0 && (
                            <span className="streak-badge">🔥 {streakDays} day streak</span>
                        )}
                    </div>

                    <div className="progress-section">
                        <div className="progress-bar">
                            <div
                                className="progress-fill"
                                style={{ width: `${progressPercentage}%` }}
                            ></div>
                        </div>
                        <span className="progress-text">{Math.round(progressPercentage)}% Complete</span>
                    </div>

                    {/* AP2 — Share + Export buttons */}
                    <div className="path-actions">
                        <button className="btn btn-share" onClick={handleShare}>
                            {copied ? '✓ Link copied!' : '🔗 Share'}
                        </button>
                        <button className="btn btn-export" onClick={handleExport}>
                            📥 Export .md
                        </button>
                    </div>
                </div>
            </div>

            <div className="milestones-container">
                <h2 className="milestones-title fade-in">Your Learning Journey</h2>

                <div className="milestones-timeline">
                    {milestones.map((milestone, index) => (
                        <div
                            key={milestone.id}
                            className={`milestone-card glass-card fade-in ${milestone.completed ? 'completed' : ''}`}
                            style={{ animationDelay: `${index * 0.1}s` }}
                        >
                            <div className="milestone-header" onClick={() => toggleMilestone(milestone.id)}>
                                <div className="milestone-number">
                                    {milestone.completed ? '✓' : index + 1}
                                </div>

                                <div className="milestone-info">
                                    <h3 className="milestone-title">{milestone.title}</h3>
                                    <div className="milestone-meta">
                                        <span className="milestone-hours">⏱️ {milestone.estimated_hours}h</span>
                                        {milestone.completed && (
                                            <span className="badge badge-success">Completed</span>
                                        )}
                                    </div>
                                </div>

                                <div className="milestone-actions">
                                    <button
                                        className="checkbox-button"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            toggleComplete(milestone);
                                        }}
                                    >
                                        <div className={`checkbox ${milestone.completed ? 'checked' : ''}`}>
                                            {milestone.completed && '✓'}
                                        </div>
                                    </button>

                                    <button className="expand-button">
                                        {expandedMilestone === milestone.id ? '▼' : '▶'}
                                    </button>
                                </div>
                            </div>

                            {/* AP5 — inline difficulty-feedback panel after completion */}
                            {feedbackPromptId === milestone.id && milestone.completed && (
                                <div className="feedback-panel fade-in">
                                    <p className="feedback-prompt">How was this milestone?</p>
                                    <div className="feedback-actions">
                                        <button
                                            className="btn btn-feedback"
                                            disabled={feedbackSubmitting}
                                            onClick={(e) => { e.stopPropagation(); submitFeedback(milestone, 'too_easy'); }}
                                        >
                                            😴 Too easy
                                        </button>
                                        <button
                                            className="btn btn-feedback"
                                            disabled={feedbackSubmitting}
                                            onClick={(e) => { e.stopPropagation(); submitFeedback(milestone, 'just_right'); }}
                                        >
                                            👍 Just right
                                        </button>
                                        <button
                                            className="btn btn-feedback"
                                            disabled={feedbackSubmitting}
                                            onClick={(e) => { e.stopPropagation(); submitFeedback(milestone, 'too_hard'); }}
                                        >
                                            🥵 Too hard
                                        </button>
                                    </div>
                                </div>
                            )}

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
                                                        <a
                                                            key={idx}
                                                            href={obj.url}
                                                            target="_blank"
                                                            rel="noopener noreferrer"
                                                            className="resource-chip"
                                                        >
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
                                        <p className="resource-disclaimer">Links are AI-suggested — verify before using.</p>
                                    </div>

                                    {/* AP8 — gated milestone completion via quiz. Signed-in users
                                        complete via quiz; signed-out fallback is the existing
                                        toggleComplete checkbox handler that's wired further up. */}
                                    {!milestone.completed && (
                                        <div style={{ marginTop: '1rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                                            {user ? (
                                                <button
                                                    className="btn btn-primary"
                                                    onClick={() => setQuizForMilestoneId(milestone.id)}
                                                >
                                                    🎯 Take quiz to complete
                                                </button>
                                            ) : (
                                                <button className="btn btn-secondary" onClick={onSignIn}>
                                                    Sign in to take the comprehension quiz
                                                </button>
                                            )}
                                            <button
                                                className="btn btn-ghost"
                                                onClick={() => toggleComplete(milestone)}
                                                style={{ fontSize: '0.8rem', color: '#94a3b8', background: 'transparent', border: 'none', cursor: 'pointer' }}
                                                title="Skip the quiz and just mark complete"
                                            >
                                                or skip the quiz
                                            </button>
                                        </div>
                                    )}

                                    {/* AP12 — per-milestone reflection field */}
                                    <MilestoneNotes
                                        milestoneId={milestone.id}
                                        signedIn={!!user}
                                        onSignIn={onSignIn}
                                    />
                                </div>
                            )}

                            {index < milestones.length - 1 && (
                                <div className="timeline-connector"></div>
                            )}
                        </div>
                    ))}
                </div>
            </div>

            {/* Floating Action Button */}
            <button className="fab btn btn-primary" onClick={onRefresh} title="Generate New Path">
                ✨ New Path
            </button>
        </div>
    );
};

export default LearningPath;
