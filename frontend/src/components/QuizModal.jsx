// AP8 — milestone quiz modal. Three states:
//   1) loading           ← initial fetch / generation
//   2) taking            ← user picks answers
//   3) result            ← server-graded result + retry / dismiss
//
// Sign-in is enforced by the GET endpoint (401); we don't render the
// modal at all for anon users (LearningPath gates the trigger button).

import React, { useEffect, useState } from 'react';
import {
    getMilestoneQuiz,
    regenerateMilestoneQuiz,
    submitQuizAttempt,
} from '../services/auth';

export default function QuizModal({ milestoneId, onClose, onPassed }) {
    const [phase, setPhase] = useState('loading'); // loading | taking | result | error
    const [error, setError] = useState('');
    const [questions, setQuestions] = useState([]);
    const [answers, setAnswers] = useState([]);
    const [submitting, setSubmitting] = useState(false);
    const [result, setResult] = useState(null);
    const [regenBusy, setRegenBusy] = useState(false);

    const load = async () => {
        setPhase('loading'); setError(''); setResult(null);
        try {
            const body = await getMilestoneQuiz(milestoneId);
            setQuestions(body.questions || []);
            setAnswers(new Array((body.questions || []).length).fill(-1));
            setPhase('taking');
        } catch (err) {
            setError(err.message || 'Could not load the quiz.');
            setPhase('error');
        }
    };

    useEffect(() => { load(); /* eslint-disable-next-line */ }, [milestoneId]);

    const allAnswered = answers.length > 0 && answers.every((a) => a >= 0);

    const submit = async () => {
        setSubmitting(true);
        try {
            const r = await submitQuizAttempt(milestoneId, answers);
            setResult(r);
            setPhase('result');
            if (r.passed && onPassed) onPassed(r);
        } catch (err) {
            setError(err.message || 'Could not submit your answers.');
            setPhase('error');
        } finally {
            setSubmitting(false);
        }
    };

    const regenerate = async () => {
        setRegenBusy(true);
        try {
            const body = await regenerateMilestoneQuiz(milestoneId);
            setQuestions(body.questions);
            setAnswers(new Array(body.questions.length).fill(-1));
            setResult(null);
            setPhase('taking');
        } catch (err) {
            setError(err.message || 'Regenerate failed.');
        } finally {
            setRegenBusy(false);
        }
    };

    const retry = () => {
        // Same questions, fresh attempt
        setAnswers(new Array(questions.length).fill(-1));
        setResult(null);
        setPhase('taking');
    };

    return (
        <div style={overlay} onClick={onClose} role="dialog" aria-modal="true">
            <div style={card} onClick={(e) => e.stopPropagation()}>
                <button onClick={onClose} style={closeBtn} aria-label="Close">✕</button>

                {phase === 'loading' && (
                    <div style={{ padding: '2rem', textAlign: 'center' }}>
                        <div className="spinner" style={{ margin: '0 auto' }}></div>
                        <p style={{ color: '#94a3b8', marginTop: '1rem' }}>Generating quiz…</p>
                    </div>
                )}

                {phase === 'error' && (
                    <div style={{ padding: '2rem' }}>
                        <h2>Couldn't load quiz</h2>
                        <p style={{ color: '#f87171' }}>{error}</p>
                        <button className="btn btn-secondary" onClick={onClose} style={{ marginTop: '1rem' }}>Close</button>
                    </div>
                )}

                {phase === 'taking' && (
                    <div style={{ padding: '1.5rem 1.75rem' }}>
                        <h2 style={{ margin: '0 0 0.25rem' }}>Quick comprehension check</h2>
                        <p style={{ color: '#94a3b8', marginTop: 0, marginBottom: '1.5rem', fontSize: '0.85rem' }}>
                            Pass {Math.round(0.7 * 100)}% to mark this milestone complete and earn XP.
                        </p>
                        {questions.map((q, i) => (
                            <div key={i} style={{ marginBottom: '1.25rem' }}>
                                <p style={{ fontWeight: 600, marginBottom: '0.5rem' }}>
                                    {i + 1}. {q.question}
                                </p>
                                <div style={{ display: 'grid', gap: '0.4rem' }}>
                                    {q.options.map((opt, oi) => {
                                        const selected = answers[i] === oi;
                                        return (
                                            <label key={oi} style={optionRow(selected)}>
                                                <input
                                                    type="radio"
                                                    name={`q-${i}`}
                                                    checked={selected}
                                                    onChange={() => setAnswers((prev) => prev.map((a, ix) => ix === i ? oi : a))}
                                                    style={{ marginRight: '0.6rem' }}
                                                />
                                                <span>{opt}</span>
                                            </label>
                                        );
                                    })}
                                </div>
                            </div>
                        ))}
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '1rem' }}>
                            <button
                                className="btn btn-ghost"
                                onClick={regenerate}
                                disabled={regenBusy}
                                style={{ fontSize: '0.8rem', color: '#94a3b8', background: 'transparent', border: 'none', cursor: 'pointer' }}
                                title="Regenerate (1/day)"
                            >
                                {regenBusy ? 'Regenerating…' : '↻ Regenerate questions'}
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={submit}
                                disabled={!allAnswered || submitting}
                            >
                                {submitting ? 'Submitting…' : 'Submit'}
                            </button>
                        </div>
                    </div>
                )}

                {phase === 'result' && result && (
                    <div style={{ padding: '1.5rem 1.75rem' }}>
                        <h2 style={{ marginTop: 0 }}>
                            {result.passed ? '🎉 Passed!' : '🤔 Not quite'}
                        </h2>
                        <p style={{ fontSize: '1.1rem', color: result.passed ? '#22c55e' : '#f87171' }}>
                            {Math.round(result.score * 100)}% correct
                        </p>
                        {result.milestone_completed && (
                            <p style={{ color: '#94a3b8', fontSize: '0.85rem' }}>
                                Milestone marked complete. ⭐ {result.total_xp} XP · 🔥 {result.streak_days || 0} day streak
                            </p>
                        )}
                        <hr style={{ borderColor: 'rgba(255,255,255,0.1)', margin: '1rem 0' }} />
                        {result.results.map((r, i) => (
                            <div key={i} style={{ marginBottom: '0.75rem', fontSize: '0.9rem' }}>
                                <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem' }}>
                                    <span style={{ color: r.correct ? '#22c55e' : '#f87171' }}>
                                        {r.correct ? '✓' : '✗'}
                                    </span>
                                    <span style={{ fontWeight: 600 }}>Q{i + 1}</span>
                                    {!r.correct && (
                                        <span style={{ color: '#94a3b8', fontSize: '0.8rem' }}>
                                            — correct answer was option {r.correct_index + 1}
                                        </span>
                                    )}
                                </div>
                                {r.explanation && (
                                    <p style={{ margin: '0.25rem 0 0 1.5rem', color: '#cbd5e1', fontSize: '0.85rem' }}>
                                        {r.explanation}
                                    </p>
                                )}
                            </div>
                        ))}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1rem' }}>
                            {!result.passed && <button className="btn btn-secondary" onClick={retry}>Retry</button>}
                            <button className="btn btn-primary" onClick={onClose}>Done</button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

const overlay = {
    position: 'fixed', inset: 0, zIndex: 100,
    background: 'rgba(0,0,0,0.6)', display: 'flex',
    alignItems: 'center', justifyContent: 'center', padding: '1rem',
    overflowY: 'auto',
};
const card = {
    background: '#1e293b',
    color: '#f8fafc',
    borderRadius: '12px',
    maxWidth: '560px', width: '100%',
    maxHeight: '90vh', overflowY: 'auto',
    border: '1px solid rgba(255,255,255,0.1)',
    position: 'relative',
};
const closeBtn = {
    position: 'absolute', top: '0.75rem', right: '0.75rem',
    background: 'transparent', border: 'none', color: '#94a3b8',
    fontSize: '1.2rem', cursor: 'pointer', padding: '0.25rem 0.5rem',
};
const optionRow = (selected) => ({
    display: 'flex', alignItems: 'center',
    padding: '0.5rem 0.75rem', borderRadius: '6px', cursor: 'pointer',
    background: selected ? 'rgba(99, 102, 241, 0.15)' : 'rgba(255,255,255,0.04)',
    border: `1px solid ${selected ? 'rgba(99, 102, 241, 0.5)' : 'rgba(255,255,255,0.08)'}`,
});
