import React, { useState, useEffect } from 'react';
import LandingPage from './components/LandingPage';
import LearningPath from './components/LearningPath';
import SharePathPage from './components/SharePathPage';
import ExplorePage from './components/ExplorePage';
import './index.css';

const API_BASE = 'http://localhost:8000/api';

// Check if the URL is a share link: /share/:pathId
function getSharePathId() {
    const match = window.location.pathname.match(/\/share\/(\d+)/);
    return match ? parseInt(match[1], 10) : null;
}

// Check if the URL is the explore page
function isExploreUrl() {
    return /^\/explore\/?$/.test(window.location.pathname);
}

function App() {
    const sharePathId = getSharePathId();
    const exploreRoute = isExploreUrl();
    const initialView = sharePathId ? 'share' : (exploreRoute ? 'explore' : 'landing');
    const [currentPath, setCurrentPath] = useState(null);
    const [view, setView] = useState(initialView);
    const [existingPaths, setExistingPaths] = useState([]);
    const [resumePrompt, setResumePrompt] = useState(null);

    // On mount: fetch existing paths to power the resume prompt
    useEffect(() => {
        if (sharePathId || exploreRoute) return; // Skip for share/explore pages
        fetch(`${API_BASE}/paths`)
            .then((r) => r.json())
            .then((paths) => {
                if (Array.isArray(paths) && paths.length > 0) {
                    setExistingPaths(paths);
                    setResumePrompt(paths[0]);
                }
            })
            .catch(() => {});
    }, []);

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

    // AP2 — render shared path page
    if (view === 'share' && sharePathId) {
        return (
            <div className="app">
                <SharePathPage pathId={sharePathId} />
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

    // AP7 — total duration badge on the resume banner
    const resumeTotalHours = resumePrompt && Array.isArray(resumePrompt.milestones)
        ? resumePrompt.milestones.reduce((s, m) => s + (m.estimated_hours || 0), 0)
        : 0;

    return (
        <div className="app">
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
                />
            )}
        </div>
    );
}

export default App;
