import React, { useState } from 'react';
import axios from 'axios';
import './LandingPage.css';

const LandingPage = ({ onPathGenerated }) => {
    const [formData, setFormData] = useState({
        goal: '',
        experience_level: 'beginner',
        time_commitment: '5-10 hours/week'
    });
    const [isGenerating, setIsGenerating] = useState(false);
    const [error, setError] = useState(null);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError(null);
        setIsGenerating(true);

        try {
            const response = await axios.post('http://localhost:8000/api/generate', formData);
            onPathGenerated(response.data);
        } catch (err) {
            setError(err.response?.data?.detail || 'Failed to generate learning path. Please try again.');
            console.error('Error generating path:', err);
        } finally {
            setIsGenerating(false);
        }
    };

    const handleChange = (e) => {
        setFormData({
            ...formData,
            [e.target.name]: e.target.value
        });
    };

    return (
        <div className="landing-page">
            <div className="hero-section">
                <div className="hero-content">
                    <div className="hero-badge fade-in">
                        <span className="badge badge-primary">✨ AI-Powered Learning</span>
                    </div>

                    <h1 className="hero-title fade-in">
                        Master Any Skill with
                        <span className="gradient-text"> AI-Generated</span>
                        <br />
                        Learning Paths
                    </h1>

                    <p className="hero-subtitle fade-in">
                        Transform your learning goals into structured, actionable roadmaps.
                        Get personalized milestones, time estimates, and curated resources—all powered by AI.
                    </p>

                    <form className="generation-form glass-card fade-in" onSubmit={handleSubmit}>
                        <div className="input-group">
                            <label className="input-label" htmlFor="goal">
                                What do you want to learn?
                            </label>
                            <textarea
                                id="goal"
                                name="goal"
                                className="input-field"
                                placeholder="e.g., Full-Stack Web Development, Machine Learning, Digital Marketing..."
                                value={formData.goal}
                                onChange={handleChange}
                                required
                                disabled={isGenerating}
                            />
                        </div>

                        <div className="form-row">
                            <div className="input-group">
                                <label className="input-label" htmlFor="experience_level">
                                    Experience Level
                                </label>
                                <select
                                    id="experience_level"
                                    name="experience_level"
                                    className="input-field"
                                    value={formData.experience_level}
                                    onChange={handleChange}
                                    disabled={isGenerating}
                                >
                                    <option value="beginner">Beginner</option>
                                    <option value="intermediate">Intermediate</option>
                                    <option value="advanced">Advanced</option>
                                </select>
                            </div>

                            <div className="input-group">
                                <label className="input-label" htmlFor="time_commitment">
                                    Time Commitment
                                </label>
                                <select
                                    id="time_commitment"
                                    name="time_commitment"
                                    className="input-field"
                                    value={formData.time_commitment}
                                    onChange={handleChange}
                                    disabled={isGenerating}
                                >
                                    <option value="1-5 hours/week">1-5 hours/week</option>
                                    <option value="5-10 hours/week">5-10 hours/week</option>
                                    <option value="10-20 hours/week">10-20 hours/week</option>
                                    <option value="20+ hours/week">20+ hours/week</option>
                                </select>
                            </div>
                        </div>

                        {error && (
                            <div className="error-message">
                                <span>⚠️ {error}</span>
                            </div>
                        )}

                        <button
                            type="submit"
                            className="btn btn-primary btn-large"
                            disabled={isGenerating || !formData.goal.trim()}
                        >
                            {isGenerating ? (
                                <>
                                    <span className="spinner"></span>
                                    <span style={{ marginLeft: '0.5rem' }}>Generating Your Path...</span>
                                </>
                            ) : (
                                <>
                                    <span>🚀 Generate Learning Path</span>
                                </>
                            )}
                        </button>
                    </form>

                    <div className="features-grid fade-in">
                        <div className="feature-card glass-card">
                            <div className="feature-icon">🎯</div>
                            <h3>Structured Milestones</h3>
                            <p>Break down complex goals into achievable steps</p>
                        </div>
                        <div className="feature-card glass-card">
                            <div className="feature-icon">⏱️</div>
                            <h3>Time Estimates</h3>
                            <p>Know exactly how long each milestone will take</p>
                        </div>
                        <div className="feature-card glass-card">
                            <div className="feature-icon">📚</div>
                            <h3>Curated Resources</h3>
                            <p>Get the best learning materials for each step</p>
                        </div>
                        <div className="feature-card glass-card">
                            <div className="feature-icon">✅</div>
                            <h3>Track Progress</h3>
                            <p>Mark milestones complete and see your journey</p>
                        </div>
                    </div>
                </div>
            </div>

            {/* Animated Background */}
            <div className="bg-gradient-1"></div>
            <div className="bg-gradient-2"></div>
            <div className="bg-gradient-3"></div>
        </div>
    );
};

export default LandingPage;
