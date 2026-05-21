import React, { useState, useEffect } from 'react';
import LandingPage from './components/LandingPage';
import LearningPath from './components/LearningPath';
import SharePathPage from './components/SharePathPage';
import ExplorePage from './components/ExplorePage';
import LoginPage from './components/LoginPage';
import MyPathsPage from './components/MyPathsPage';
import ProfilePage from './components/ProfilePage';
import AuthBar from './components/AuthBar';
import { getCurrentUser, logout, verifyToken } from './services/auth';
import './index.css';

const API_BASE = 'http://localhost:8000/api';

// Check if the URL is a share link: /share/:pathId
function getSharePathId() {
    const match = window.location.pathname.match(/\/share\/(\d+)/);
    return match ? parseInt(match[1], 10) : null;
}

function isExploreUrl()  { return /^\/explore\/?$/.test(window.location.pathname); }
function isLoginUrl()    { return /^\/login\/?$/.test(window.location.pathname); }
function isMyPathsUrl()  { return /^\/my-paths\/?$/.test(window.location.pathname); }
function isProfileUrl()  { return /^\/profile\/?$/.test(window.location.pathname); }
function isVerifyUrl()   { return /^\/auth\/verify\/?$/.test(window.location.pathname); }
function getVerifyToken() {
    const params = new URLSearchParams(window.location.search);
    return params.get('token');
}

function pickInitialView() {
    if (getSharePathId()) return 'share';
    if (isExploreUrl())  return 'explore';
    if (isLoginUrl())    return 'login';
    if (isMyPathsUrl())  return 'my-paths';
    if (isProfileUrl())  return 'profile';
    if (isVerifyUrl())   return 'verify';
    return 'landing';
}

function App() {
    const sharePathId = getSharePathId();
    const exploreRoute = isExploreUrl();
    const [currentPath, setCurrentPath] = useState(null);
    const [view, setView] = useState(pickInitialView());
    const [existingPaths, setExistingPaths] = useState([]);
    const [resumePrompt, setResumePrompt] = useState(null);
    const [user, setUser] = useState(null);
    const [verifyError, setVerifyError] = useState('');

    // AP9 — load current user on mount
    useEffect(() => {
        getCurrentUser().then(setUser).catch(() => setUser(null));
    }, []);

    // AP9 — handle /auth/verify?token=... by exchanging the token for a session
    useEffect(() => {
        if (view !== 'verify') return;
        const token = getVerifyToken();
        if (!token) {
            setVerifyError('No token found in the link.');
            return;
        }
        verifyToken(token)
            .then((u) => {
                setUser(u);
                window.history.replaceState({}, '', '/');
                setView('landing');
            })
            .catch((err) => setVerifyError(err.message || 'Sign-in link is invalid or expired.'));
    }, [view]);

    // On mount: fetch existing paths to power the resume prompt.
    // Refetch when `user` changes so signing in surfaces the user's paths
    // and signing out clears the banner.
    useEffect(() => {
        if (sharePathId || exploreRoute) return;
        fetch(`${API_BASE}/paths`, { credentials: 'include' })
            .then((r) => r.json())
            .then((paths) => {
                if (Array.isArray(paths) && paths.length > 0) {
                    setExistingPaths(paths);
                    setResumePrompt(paths[0]);
                } else {
                    setExistingPaths([]);
                    setResumePrompt(null);
                }
            })
            .catch(() => {});
    }, [user]);

    const handlePathGenerated = (pathData) => {
        setCurrentPath(pathData);
        setResumePrompt(null);
        setView('path');
    };

    const handleResume = () => {
        setCurrentPath(resumePrompt);
        setResumePrompt(null);
        setView('path');
    };

    const handleDismissResume = () => {
        setResumePrompt(null);
    };

    const handleBack = () => {
        setView('landing');
    };

    const handleRefresh = () => {
        setView('landing');
        setCurrentPath(null);
    };

    const handleExplore = () => {
        window.history.pushState({}, '', '/explore');
        setView('explore');
    };

    const handleBackFromExplore = () => {
        window.history.pushState({}, '', '/');
        setView('landing');
    };

    // AP9 — auth navigation
    const handleSignIn = () => {
        window.history.pushState({}, '', '/login');
        setView('login');
    };
    const handleSignOut = async () => {
        await logout();
        setUser(null);
        setExistingPaths([]);
        setResumePrompt(null);
    };
    const handleShowMyPaths = () => {
        window.history.pushState({}, '', '/my-paths');
        setView('my-paths');
    };
    const handleShowProfile = () => {
        if (!user) { handleSignIn(); return; }
        window.history.pushState({}, '', '/profile');
        setView('profile');
    };
    const handleBackToLanding = () => {
        window.history.pushState({}, '', '/');
        setView('landing');
    };

    // AP2 — render shared path page
    if (view === 'share' && sharePathId) {
        return (
            <div className="app">
                <AuthBar user={user} onSignIn={handleSignIn} onSignOut={handleSignOut} onMyPaths={handleShowMyPaths} onProfile={handleShowProfile} />
                <SharePathPage
                    pathId={sharePathId}
                    user={user}
                    onSignIn={handleSignIn}
                    onForked={(newPath) => {
                        // AP10 — drop the user into their freshly-forked editable copy
                        setCurrentPath(newPath);
                        window.history.replaceState({}, '', '/');
                        setView('path');
                    }}
                />
            </div>
        );
    }

    // AP6 — render explore page
    if (view === 'explore') {
        return (
            <div className="app">
                <ExplorePage onBack={handleBackFromExplore} />
            </div>
        );
    }

    // AP9 — login route
    if (view === 'login') {
        return (
            <div className="app">
                <AuthBar user={user} onSignIn={handleSignIn} onSignOut={handleSignOut} onMyPaths={handleShowMyPaths} onProfile={handleShowProfile} />
                <LoginPage onBack={handleBackToLanding} />
            </div>
        );
    }

    // AP9 — verifying magic link
    if (view === 'verify') {
        return (
            <div className="app" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '60vh' }}>
                <div className="glass-card fade-in" style={{ padding: '2rem', textAlign: 'center', maxWidth: '420px' }}>
                    {verifyError ? (
                        <>
                            <h2>Sign-in failed</h2>
                            <p style={{ color: '#f87171' }}>{verifyError}</p>
                            <button className="btn btn-primary" onClick={handleSignIn} style={{ marginTop: '1rem' }}>Try again</button>
                        </>
                    ) : (
                        <p>Signing you in…</p>
                    )}
                </div>
            </div>
        );
    }

    // AP26 — profile + achievement badges (owner only)
    if (view === 'profile') {
        return (
            <div className="app">
                <AuthBar user={user} onSignIn={handleSignIn} onSignOut={handleSignOut} onMyPaths={handleShowMyPaths} onProfile={handleShowProfile} />
                <ProfilePage user={user} onBack={handleBackToLanding} />
            </div>
        );
    }

    // AP9 — my saved paths
    if (view === 'my-paths') {
        return (
            <div className="app">
                <AuthBar user={user} onSignIn={handleSignIn} onSignOut={handleSignOut} onMyPaths={handleShowMyPaths} onProfile={handleShowProfile} />
                <MyPathsPage
                    user={user}
                    onUserUpdate={setUser}
                    onPick={(p) => { setCurrentPath(p); setView('path'); window.history.pushState({}, '', '/'); }}
                    onBack={handleBackToLanding}
                />
            </div>
        );
    }

    // AP7 — total duration badge on the resume banner
    const resumeTotalHours = resumePrompt && Array.isArray(resumePrompt.milestones)
        ? resumePrompt.milestones.reduce((s, m) => s + (m.estimated_hours || 0), 0)
        : 0;

    return (
        <div className="app">
            <AuthBar user={user} onSignIn={handleSignIn} onSignOut={handleSignOut} onMyPaths={handleShowMyPaths} onProfile={handleShowProfile} />
            {/* Resume-path banner */}
            {view === 'landing' && resumePrompt && (
                <div className="resume-banner glass-card fade-in">
                    <p>
                        Resume your learning path on{' '}
                        <strong>{resumePrompt.title}</strong>?
                        {resumeTotalHours > 0 && (
                            <span className="duration-badge">⏱ {resumeTotalHours}h</span>
                        )}
                    </p>
                    <div className="resume-banner-actions">
                        <button className="btn btn-primary" onClick={handleResume}>
                            Continue
                        </button>
                        <button className="btn btn-secondary" onClick={handleDismissResume}>
                            Start new
                        </button>
                    </div>
                </div>
            )}

            {view === 'landing' ? (
                <LandingPage onPathGenerated={handlePathGenerated} onExplore={handleExplore} />
            ) : (
                <LearningPath
                    pathData={currentPath}
                    onBack={handleBack}
                    onRefresh={handleRefresh}
                    user={user}
                    onSignIn={handleSignIn}
                />
            )}
        </div>
    );
}

export default App;
