// AP9 — frontend auth client. All calls include cookies so the session
// cookie + anon_session_id cookie ride along.

const API_BASE = 'http://localhost:8000/api';

async function jsonOrError(res) {
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const detail = body?.detail || body?.message || res.statusText || 'Request failed';
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return body;
}

export async function requestMagicLink(email) {
  const res = await fetch(`${API_BASE}/auth/request-link`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  return jsonOrError(res);
}

export async function verifyToken(token) {
  const res = await fetch(`${API_BASE}/auth/verify`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  return jsonOrError(res);
}

export async function getCurrentUser() {
  const res = await fetch(`${API_BASE}/auth/me`, {
    credentials: 'include',
  });
  if (!res.ok) return null;
  return res.json();
}

export async function logout() {
  await fetch(`${API_BASE}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  });
}

export async function fetchMyPaths() {
  const res = await fetch(`${API_BASE}/paths/me`, {
    credentials: 'include',
  });
  return jsonOrError(res);
}

// AP10 — fork a public path into the caller's account.
export async function forkPath(pathId) {
  const res = await fetch(`${API_BASE}/paths/${pathId}/fork`, {
    method: 'POST',
    credentials: 'include',
  });
  return jsonOrError(res);
}

// AP11 — daily-reminder opt-in toggle. Requires sign-in.
export async function setReminderOptIn(optIn) {
  const res = await fetch(`${API_BASE}/auth/me/reminders`, {
    method: 'PATCH',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reminder_opt_in: !!optIn }),
  });
  return jsonOrError(res);
}

// AP8 — milestone quizzes (auth required)
export async function getMilestoneQuiz(milestoneId) {
  const res = await fetch(`${API_BASE}/milestones/${milestoneId}/quiz`, {
    credentials: 'include',
  });
  return jsonOrError(res);
}

export async function regenerateMilestoneQuiz(milestoneId) {
  const res = await fetch(`${API_BASE}/milestones/${milestoneId}/quiz/regenerate`, {
    method: 'POST',
    credentials: 'include',
  });
  return jsonOrError(res);
}

export async function submitQuizAttempt(milestoneId, answers) {
  const res = await fetch(`${API_BASE}/milestones/${milestoneId}/quiz/attempt`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answers }),
  });
  return jsonOrError(res);
}

// AP12 — milestone notes (auth required for read/write of own; public for share-page reads)
export async function getMyNote(milestoneId) {
  const res = await fetch(`${API_BASE}/milestones/${milestoneId}/note`, {
    credentials: 'include',
  });
  if (res.status === 401) return null;
  return jsonOrError(res);
}

export async function saveMyNote(milestoneId, content, isPrivate) {
  const res = await fetch(`${API_BASE}/milestones/${milestoneId}/note`, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, is_private: !!isPrivate }),
  });
  return jsonOrError(res);
}

export async function getPublicNotes(pathId) {
  const res = await fetch(`${API_BASE}/paths/${pathId}/notes/public`);
  if (!res.ok) return {};
  return res.json();
}
