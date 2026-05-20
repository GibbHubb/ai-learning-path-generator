// AP9 — magic-link sign-in flow. Two states:
//   1) Email entry → POST /auth/request-link
//   2) "Check your inbox" confirmation
//
// Verification of the email link itself happens in App.jsx — when the
// browser opens /auth/verify?token=..., App calls /api/auth/verify.

import React, { useState } from 'react';
import { requestMagicLink } from '../services/auth';

export default function LoginPage({ onBack }) {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!email.trim()) return;
    setSubmitting(true);
    setError('');
    try {
      await requestMagicLink(email.trim());
      setSent(true);
    } catch (err) {
      setError(err.message || 'Could not send sign-in link.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-page" style={pageStyle}>
      <div className="glass-card fade-in" style={cardStyle}>
        {!sent ? (
          <>
            <h1 style={{ marginBottom: '0.5rem' }}>Sign in</h1>
            <p style={{ color: '#94a3b8', marginBottom: '1.5rem' }}>
              We'll email you a one-tap sign-in link. No password needed.
            </p>
            <form onSubmit={handleSubmit}>
              <label style={labelStyle}>
                Email
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoFocus
                  placeholder="you@example.com"
                  style={inputStyle}
                  disabled={submitting}
                />
              </label>
              {error && <p style={errorStyle}>{error}</p>}
              <button
                type="submit"
                className="btn btn-primary"
                disabled={submitting || !email.trim()}
                style={{ width: '100%', marginTop: '1rem' }}
              >
                {submitting ? 'Sending…' : 'Email me a link'}
              </button>
            </form>
          </>
        ) : (
          <>
            <h1>Check your inbox 📬</h1>
            <p style={{ color: '#94a3b8', marginBottom: '1.5rem' }}>
              We sent a sign-in link to <strong>{email}</strong>.
              Click the link in your email to finish signing in. The link
              expires in 15 minutes.
            </p>
            <p style={{ color: '#64748b', fontSize: '0.85rem' }}>
              Didn't get it? Check spam, or wait a minute and try again.
            </p>
          </>
        )}
        {onBack && (
          <button
            onClick={onBack}
            className="btn btn-secondary"
            style={{ marginTop: '1rem', width: '100%' }}
          >
            ← Back
          </button>
        )}
      </div>
    </div>
  );
}

const pageStyle = { display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '70vh', padding: '1rem' };
const cardStyle = { maxWidth: '400px', width: '100%', padding: '2rem' };
const labelStyle = { display: 'block', fontSize: '0.85rem', color: '#cbd5e1' };
const inputStyle = {
  display: 'block', width: '100%', marginTop: '0.5rem',
  padding: '0.75rem', borderRadius: '8px',
  background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.15)',
  color: 'inherit', fontSize: '1rem',
};
const errorStyle = { color: '#f87171', fontSize: '0.85rem', marginTop: '0.5rem' };
