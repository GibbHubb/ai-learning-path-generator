import React, { useState, useEffect } from 'react';
import LandingPage from './components/LandingPage';
import LearningPath from './components/LearningPath';
import SharePathPage from './components/SharePathPage';
import './index.css';

const API_BASE = 'http://localhost:8000/api';

// Check if the URL is a share link: /share/:pathId
function getSharePathId() {
    const match = window.location.pathname.match(/\/share\/(\d+)/);
    return match ? parseInt(match[1], 10) : null;
}

function App() {
    const sharePathId = getSharePathId();
    const [currentPath, setCurrentPath] = useState(null);
    const [view, setView] = useState(sharePathId ? 'share' : 'landing');
    const [existingPaths, setExistingPaths] = useState([]);
    const [resumePrompt, setResumePrompt] = useState(null);

    // On mount: fetch existing paths to power the resume prompt
    useEffect(() => {
        if (sharePathId) return; // Skip for share pages
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

    // AP2 — render shared path page
    if (view === 'share' && sharePathId) {
        return (
            <div className="app">
                <SharePathPage pathId={sharePathId} />
            </div>
        );
    }

    return (
        <div className="app">
            {/* Resume-path banner */}
            {view === 'landing' && resumePrompt && (
                <div className="resume-banner glass-card fade-in">
                    <p>
                        Resume your learning path on{' '}
                        <strong>{resumePrompt.title}</strong>?
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
                <LandingPage onPathGenerated={handlePathGenerated} />
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
