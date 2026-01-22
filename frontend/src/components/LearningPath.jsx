import React, { useState } from 'react';
import axios from 'axios';
import './LearningPath.css';

const LearningPath = ({ pathData, onBack, onRefresh }) => {
    const [milestones, setMilestones] = useState(pathData.milestones || []);
    const [expandedMilestone, setExpandedMilestone] = useState(null);

    const toggleMilestone = (milestoneId) => {
        setExpandedMilestone(expandedMilestone === milestoneId ? null : milestoneId);
    };

    const toggleComplete = async (milestone) => {
        try {
            await axios.patch(`http://localhost:8000/api/milestones/${milestone.id}`, {
                completed: !milestone.completed
            });

            setMilestones(milestones.map(m =>
                m.id === milestone.id
                    ? { ...m, completed: !m.completed }
                    : m
            ));
        } catch (error) {
            console.error('Error updating milestone:', error);
        }
    };

    const completedCount = milestones.filter(m => m.completed).length;
    const totalCount = milestones.length;
    const progressPercentage = (completedCount / totalCount) * 100;
    const totalHours = milestones.reduce((sum, m) => sum + m.estimated_hours, 0);

    return (
        <div className="learning-path-container">
            <div className="path-header glass-card fade-in">
                <button className="btn btn-secondary back-button" onClick={onBack}>
                    ← Back
                </button>

                <div className="path-header-content">
                    <h1 className="path-title">{pathData.title}</h1>
                    <p className="path-description">{pathData.description}</p>

                    <div className="path-meta">
                        <span className="badge badge-primary">{pathData.experience_level}</span>
                        <span className="meta-item">⏱️ {pathData.time_commitment}</span>
                        <span className="meta-item">📚 {totalHours} total hours</span>
                        <span className="meta-item">
                            ✅ {completedCount}/{totalCount} completed
                        </span>
                    </div>

                    <div className="progress-section">
                        <div className="progress-bar">
                            <div
                                className="progress-fill"
                                style={{ width: `${progressPercentage}%` }}
                            ></div>
                        </div>
                        <span className="progress-text">{Math.round(progressPercentage)}% Complete</span>
                    </div>
                </div>
            </div>

            <div className="milestones-container">
                <h2 className="milestones-title fade-in">Your Learning Journey</h2>

                <div className="milestones-timeline">
                    {milestones.map((milestone, index) => (
                        <div
                            key={milestone.id}
                            className={`milestone-card glass-card fade-in ${milestone.completed ? 'completed' : ''}`}
                            style={{ animationDelay: `${index * 0.1}s` }}
                        >
                            <div className="milestone-header" onClick={() => toggleMilestone(milestone.id)}>
                                <div className="milestone-number">
                                    {milestone.completed ? '✓' : index + 1}
                                </div>

                                <div className="milestone-info">
                                    <h3 className="milestone-title">{milestone.title}</h3>
                                    <div className="milestone-meta">
                                        <span className="milestone-hours">⏱️ {milestone.estimated_hours}h</span>
                                        {milestone.completed && (
                                            <span className="badge badge-success">Completed</span>
                                        )}
                                    </div>
                                </div>

                                <div className="milestone-actions">
                                    <button
                                        className="checkbox-button"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            toggleComplete(milestone);
                                        }}
                                    >
                                        <div className={`checkbox ${milestone.completed ? 'checked' : ''}`}>
                                            {milestone.completed && '✓'}
                                        </div>
                                    </button>

                                    <button className="expand-button">
                                        {expandedMilestone === milestone.id ? '▼' : '▶'}
                                    </button>
                                </div>
                            </div>

                            {expandedMilestone === milestone.id && (
                                <div className="milestone-details">
                                    <div className="milestone-description">
                                        <h4>What You'll Learn</h4>
                                        <p>{milestone.description}</p>
                                    </div>

                                    <div className="milestone-resources">
                                        <h4>Recommended Resources</h4>
                                        <ul>
                                            {milestone.resources.map((resource, idx) => (
                                                <li key={idx}>
                                                    <span className="resource-icon">📖</span>
                                                    {resource}
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                </div>
                            )}

                            {index < milestones.length - 1 && (
                                <div className="timeline-connector"></div>
                            )}
                        </div>
                    ))}
                </div>
            </div>

            {/* Floating Action Button */}
            <button className="fab btn btn-primary" onClick={onRefresh} title="Generate New Path">
                ✨ New Path
            </button>
        </div>
    );
};

export default LearningPath;
