// AP23 — sub-task checklist under a milestone.
// Owner-only feature; signed-out users see a prompt. Toggling the last
// open task auto-completes the milestone (server-side, via the shared
// complete_milestone helper); un-ticking from all-done reverts.
import React, { useState } from 'react';
import axios from 'axios';

// AP31 — relative by default, so the SPA and the API share an origin in
// production and there is no build-time URL to get wrong. Local dev is
// unchanged: vite.config.js already proxies /api to localhost:8000.
const API_BASE = import.meta.env.VITE_API_BASE || '/api';

const inputStyle = {
    flex: 1,
    padding: '0.45rem 0.6rem',
    background: 'rgba(15, 23, 42, 0.6)',
    border: '1px solid rgba(148, 163, 184, 0.25)',
    borderRadius: '0.4rem',
    color: '#e2e8f0',
    fontSize: '0.9rem',
};

const MilestoneTasks = ({ milestoneId, initialTasks, signedIn, onSignIn, onMilestoneAutoToggle }) => {
    const [tasks, setTasks] = useState(initialTasks || []);
    const [newTitle, setNewTitle] = useState('');
    const [busy, setBusy] = useState(false);

    if (!signedIn) {
        return (
            <div style={{ marginTop: '0.75rem', fontSize: '0.85rem', color: '#94a3b8' }}>
                <button className="btn btn-ghost" onClick={onSignIn} style={{ padding: 0, background: 'transparent', border: 'none', color: '#60a5fa', cursor: 'pointer' }}>
                    Sign in
                </button>
                {' to split this milestone into a task checklist.'}
            </div>
        );
    }

    const totalCount = tasks.length;
    const doneCount = tasks.filter((t) => t.completed).length;

    const addTask = async (e) => {
        e?.preventDefault?.();
        const title = newTitle.trim();
        if (!title || busy) return;
        setBusy(true);
        try {
            const res = await axios.post(
                `${API_BASE}/milestones/${milestoneId}/tasks`,
                { title },
                { withCredentials: true },
            );
            setTasks((prev) => [...prev, res.data]);
            setNewTitle('');
        } catch (err) {
            console.warn('Failed to add task', err);
        } finally {
            setBusy(false);
        }
    };

    const toggleTask = async (task) => {
        if (busy) return;
        setBusy(true);
        // Optimistic flip
        setTasks((prev) => prev.map((t) => t.id === task.id ? { ...t, completed: !t.completed } : t));
        try {
            const res = await axios.patch(
                `${API_BASE}/tasks/${task.id}`,
                { completed: !task.completed },
                { withCredentials: true },
            );
            // Sync server's canonical state
            setTasks((prev) => prev.map((t) => t.id === res.data.task.id ? res.data.task : t));
            if ((res.data.milestone_auto_completed || res.data.milestone_auto_uncompleted) && onMilestoneAutoToggle) {
                onMilestoneAutoToggle({
                    completed: res.data.milestone_auto_completed === true,
                    total_xp: res.data.total_xp,
                    streak_days: res.data.streak_days,
                });
            }
        } catch (err) {
            // Revert on failure
            setTasks((prev) => prev.map((t) => t.id === task.id ? task : t));
            console.warn('Failed to toggle task', err);
        } finally {
            setBusy(false);
        }
    };

    const deleteTask = async (task) => {
        if (busy) return;
        setBusy(true);
        try {
            const res = await axios.delete(
                `${API_BASE}/tasks/${task.id}`,
                { withCredentials: true },
            );
            setTasks((prev) => prev.filter((t) => t.id !== task.id));
            if (res.data?.milestone_auto_completed && onMilestoneAutoToggle) {
                onMilestoneAutoToggle({ completed: true });
            }
        } catch (err) {
            console.warn('Failed to delete task', err);
        } finally {
            setBusy(false);
        }
    };

    return (
        <div style={{ marginTop: '1rem', padding: '0.75rem', background: 'rgba(30, 41, 59, 0.4)', borderRadius: '0.5rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                <span style={{ fontSize: '0.85rem', color: '#cbd5e1', fontWeight: 600 }}>Sub-tasks</span>
                {totalCount > 0 && (
                    <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>{doneCount}/{totalCount} done</span>
                )}
            </div>

            {tasks.length === 0 && (
                <div style={{ fontSize: '0.8rem', color: '#94a3b8', marginBottom: '0.5rem' }}>
                    Optional: break this milestone into smaller steps.
                </div>
            )}

            {tasks.map((t) => (
                <div key={t.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.3rem 0' }}>
                    <input
                        type="checkbox"
                        checked={!!t.completed}
                        onChange={() => toggleTask(t)}
                        disabled={busy}
                        style={{ cursor: 'pointer' }}
                    />
                    <span style={{ flex: 1, fontSize: '0.9rem', color: t.completed ? '#64748b' : '#e2e8f0', textDecoration: t.completed ? 'line-through' : 'none' }}>
                        {t.title}
                    </span>
                    <button
                        onClick={() => deleteTask(t)}
                        title="Delete task"
                        style={{ background: 'transparent', border: 'none', color: '#94a3b8', cursor: 'pointer', fontSize: '0.85rem', padding: '0.1rem 0.3rem' }}
                    >
                        ✕
                    </button>
                </div>
            ))}

            <form onSubmit={addTask} style={{ display: 'flex', gap: '0.4rem', marginTop: '0.5rem' }}>
                <input
                    type="text"
                    placeholder="Add a sub-task…"
                    value={newTitle}
                    onChange={(e) => setNewTitle(e.target.value)}
                    style={inputStyle}
                    disabled={busy}
                />
                <button
                    type="submit"
                    className="btn btn-secondary"
                    disabled={!newTitle.trim() || busy}
                    style={{ fontSize: '0.85rem', padding: '0.4rem 0.8rem' }}
                >
                    Add
                </button>
            </form>
        </div>
    );
};

export default MilestoneTasks;
