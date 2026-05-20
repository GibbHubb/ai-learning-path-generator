// AP12 — per-milestone reflection field. Auto-saves 3s after the last
// keystroke; Cmd/Ctrl+Enter saves immediately. Empty content deletes.

import React, { useEffect, useRef, useState } from 'react';
import { getMyNote, saveMyNote } from '../services/auth';

const SAVE_DEBOUNCE_MS = 3000;

export default function MilestoneNotes({ milestoneId, signedIn, onSignIn }) {
    const [content, setContent] = useState('');
    const [isPrivate, setIsPrivate] = useState(false);
    const [savedContent, setSavedContent] = useState('');
    const [status, setStatus] = useState('idle'); // 'idle' | 'saving' | 'saved' | 'error'
    const debounceRef = useRef(null);
    const mountedRef = useRef(true);

    // Initial load
    useEffect(() => {
        mountedRef.current = true;
        if (!signedIn) return;
        let cancelled = false;
        getMyNote(milestoneId)
            .then((note) => {
                if (cancelled) return;
                setContent(note?.content || '');
                setSavedContent(note?.content || '');
                setIsPrivate(!!note?.is_private);
            })
            .catch(() => {});
        return () => { cancelled = true; mountedRef.current = false; };
    }, [milestoneId, signedIn]);

    const persist = async (body, priv) => {
        setStatus('saving');
        try {
            await saveMyNote(milestoneId, body, priv);
            if (!mountedRef.current) return;
            setSavedContent(body.trim());
            setStatus('saved');
            setTimeout(() => mountedRef.current && setStatus('idle'), 1500);
        } catch {
            if (mountedRef.current) setStatus('error');
        }
    };

    // Debounced auto-save
    useEffect(() => {
        if (!signedIn) return;
        if (content.trim() === savedContent.trim()) return;  // no-op
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => persist(content, isPrivate), SAVE_DEBOUNCE_MS);
        return () => debounceRef.current && clearTimeout(debounceRef.current);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [content, isPrivate, signedIn]);

    const handleKeyDown = (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            if (debounceRef.current) clearTimeout(debounceRef.current);
            persist(content, isPrivate);
        }
    };

    if (!signedIn) {
        return (
            <div style={hintStyle}>
                <button className="btn btn-secondary" onClick={onSignIn} style={{ fontSize: '0.85rem' }}>
                    Sign in to add a reflection
                </button>
            </div>
        );
    }

    return (
        <div className="milestone-notes" style={wrapStyle}>
            <div style={headerRow}>
                <h4 style={{ margin: 0 }}>Your reflection</h4>
                <span style={statusStyle(status)}>
                    {status === 'saving' && 'Saving…'}
                    {status === 'saved' && 'Saved ✓'}
                    {status === 'error' && 'Could not save'}
                </span>
            </div>
            <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="What did you learn? What got you stuck? Anything you'd revisit?"
                rows={4}
                style={textareaStyle}
            />
            <div style={footerRow}>
                <label style={privateRow}>
                    <input
                        type="checkbox"
                        checked={isPrivate}
                        onChange={(e) => setIsPrivate(e.target.checked)}
                    />
                    <span>Private — hide from the public share page</span>
                </label>
                <span style={shortcutHint}>⌘/Ctrl + ↵ to save now</span>
            </div>
        </div>
    );
}

const wrapStyle = { marginTop: '1rem' };
const headerRow = { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.5rem' };
const textareaStyle = {
    width: '100%', padding: '0.75rem', borderRadius: '8px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.15)',
    color: 'inherit', fontSize: '0.95rem',
    fontFamily: 'inherit', resize: 'vertical',
    whiteSpace: 'pre-wrap',
};
const footerRow = { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.5rem', fontSize: '0.8rem', color: '#94a3b8' };
const privateRow = { display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' };
const shortcutHint = { color: '#64748b' };
const hintStyle = { marginTop: '1rem' };
const statusStyle = (s) => ({
    fontSize: '0.8rem',
    color: s === 'error' ? '#f87171' : s === 'saved' ? '#22c55e' : '#94a3b8',
});
