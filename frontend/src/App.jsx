import React, { useState, useEffect } from 'react';
import LandingPage from './components/LandingPage';
import LearningPath from './components/LearningPath';
import './index.css';

const API_BASE = 'http://localhost:8000/api';

function App() {
    const [currentPath, setCurrentPath] = useState(null);
    const [view, setView] = useState('landing'); // 'landing' | 'path'
    const [existingPaths, setExistingPaths] = useState([]);
    const [resumePrompt, setResumePrompt] = useState(null); // path object to resume

    // On mount: fetch existing paths to power the resume prompt
    useEffect(() => {
        fetch(`${API_BASE}/paths`)
            .then((r) => r.json())
            .then((paths) => {
                if (Array.isArray(paths) && paths.length > 0) {
                    setExistingPaths(paths);
                    // Show resume prompt for the most recent path
                    setResumePrompt(paths[0]);
                }
            })
            .catch(() => {
                // Silently ignore — backend may not be running in all environments
            });
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
