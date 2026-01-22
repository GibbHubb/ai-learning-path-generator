import React, { useState } from 'react';
import LandingPage from './components/LandingPage';
import LearningPath from './components/LearningPath';
import './index.css';

function App() {
    const [currentPath, setCurrentPath] = useState(null);
    const [view, setView] = useState('landing'); // 'landing' or 'path'

    const handlePathGenerated = (pathData) => {
        setCurrentPath(pathData);
        setView('path');
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
