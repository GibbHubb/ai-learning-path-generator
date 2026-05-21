// AP9 — small top-right auth widget. Shows user email + sign-out when
// authenticated; "Sign in" link otherwise.

import React from 'react';

export default function AuthBar({ user, onSignIn, onSignOut, onMyPaths, onProfile }) {
  return (
    <div style={barStyle}>
      {user ? (
        <>
          <button onClick={onMyPaths} style={linkStyle}>My paths</button>
          {onProfile && <button onClick={onProfile} style={linkStyle}>Profile</button>}
          <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>{user.email}</span>
          <button onClick={onSignOut} style={btnStyle}>Sign out</button>
        </>
      ) : (
        <button onClick={onSignIn} style={btnStyle}>Sign in</button>
      )}
    </div>
  );
}

const barStyle = {
  position: 'absolute', top: '1rem', right: '1rem',
  display: 'flex', gap: '0.75rem', alignItems: 'center', zIndex: 10,
};
const btnStyle = {
  padding: '0.4rem 0.9rem',
  background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.15)',
  color: 'inherit', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem',
};
const linkStyle = {
  ...btnStyle, background: 'transparent', borderColor: 'transparent',
};
