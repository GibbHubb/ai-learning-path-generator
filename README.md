# AI Learning Path Generator

> Transform your learning goals into structured, actionable roadmaps

A learning path generator that helps users master complex skills by breaking them down into structured milestones with personalized resources, time estimates, and progress tracking.

![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-green) ![React](https://img.shields.io/badge/React-18.3-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## Table of Contents

- [Problem Statement](#problem-statement)
- [Solution](#solution)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Getting Started](#getting-started)
- [Usage](#usage)
- [API Documentation](#api-documentation)
- [Design Decisions](#design-decisions)
- [Future Improvements](#future-improvements-v2)
- [Time Breakdown](#time-breakdown)

## Problem Statement

Learning complex skills is overwhelming. People struggle with:
- **Where to start**: Too many resources, unclear prerequisites
- **How to structure learning**: No clear path from beginner to proficient
- **Time management**: Unrealistic expectations about time commitment
- **Staying motivated**: Lack of visible progress and milestones

## Solution

A tool that:
1. Takes a high-level learning goal (e.g., "Learn Full-Stack Development")
2. Generates a structured learning path with 5-8 progressive milestones
3. Provides time estimates, resource recommendations, and clear descriptions
4. Enables progress tracking with visual feedback
5. Allows regeneration and refinement of paths

## Features

### Core Features
- **Intelligent Generation**: Uses GPT-4 to create personalized learning paths
- **Structured Milestones**: Each path broken into 5-8 sequential milestones
- **Resource Recommendations**: Curated resources for each milestone
- **Time Estimates**: Realistic hour estimates per milestone
- **Progress Tracking**: Mark milestones complete and visualize progress
- **Persistent Storage**: Save and retrieve learning paths
- **Experience Levels**: Tailored for beginner, intermediate, or advanced learners
- **Time Commitment**: Customize based on available hours per week

### UX Highlights
- **Dark, Modern UI**: Glassmorphism effects with vibrant gradients
- **Smooth Animations**: Fade-in effects and micro-interactions
- **Responsive Design**: Works seamlessly on desktop and mobile
- **Visual Progress**: Progress bars and completion badges
- **Expandable Milestones**: Click to see detailed descriptions and resources

## Tech Stack

### Backend
- **FastAPI** (Python): High-performance async API framework
- **SQLAlchemy**: ORM for database interactions
- **SQLite**: Lightweight, file-based database
- **OpenAI API**: GPT-4 for learning path generation
- **Pydantic**: Data validation and serialization

### Frontend
- **React 18**: Modern UI library with hooks
- **Vite**: Fast build tool and dev server
- **Axios**: HTTP client for API calls
- **Vanilla CSS**: Custom design system with CSS variables

### Why These Choices?
- **FastAPI**: Aligns with company stack, excellent async support, auto-generated docs
- **React + Vite**: Fast development, modern tooling, minimal setup
- **SQLite**: No configuration needed, perfect for MVP, easy migration to PostgreSQL
- **Vanilla CSS**: Full control, demonstrates CSS skills, no framework overhead

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Frontend (React)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ LandingPage  │  │ LearningPath │  │  App (State) │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            │
                    HTTP/JSON (Axios)
                            │
┌─────────────────────────────────────────────────────────────┐
│                      Backend (FastAPI)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Routes     │  │  AI Service  │  │   Database   │      │
│  │  (REST API)  │  │  (OpenAI)    │  │  (SQLite)    │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow
1. User submits learning goal via form
2. Frontend sends POST request to `/api/generate`
3. Backend calls OpenAI API with structured prompt
4. AI returns JSON with path title, description, and milestones
5. Backend saves to database and returns to frontend
6. Frontend displays interactive learning path
7. User can mark milestones complete (PATCH `/api/milestones/{id}`)

## Getting Started

### Prerequisites
- Python 3.8+
- Node.js 16+
- OpenAI API key

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/GibbHubb/ai-learning-path-generator.git
cd ai-learning-path-generator
```

2. **Set up the backend**
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. **Configure environment variables**
```bash
# Create .env file in project root
echo "OPENAI_API_KEY=your_api_key_here" > ../.env
```

4. **Set up the frontend**
```bash
cd ../frontend
npm install
```

### Running the Application

1. **Start the backend** (Terminal 1)
```bash
cd backend
python main.py
# Backend runs on http://localhost:8000
```

2. **Start the frontend** (Terminal 2)
```bash
cd frontend
npm run dev
# Frontend runs on http://localhost:5173
```

3. **Open your browser**
```
http://localhost:5173
```

## Usage

1. **Enter your learning goal**
   - Describe what you want to learn (e.g., "Machine Learning for Beginners")
   
2. **Select experience level**
   - Choose: Beginner, Intermediate, or Advanced
   
3. **Set time commitment**
   - Select weekly hours available for learning
   
4. **Generate path**
   - Click "Generate Learning Path" and wait ~5-10 seconds
   
5. **Explore your path**
   - View structured milestones in timeline format
   - Click milestones to expand and see details
   - Check off completed milestones
   - Track overall progress

6. **Generate new paths**
   - Click "New Path" to create another learning roadmap

## API Documentation

### Endpoints

#### `POST /api/generate`
Generate a new learning path

**Request Body:**
```json
{
  "goal": "Learn Full-Stack Web Development",
  "experience_level": "beginner",
  "time_commitment": "10-20 hours/week"
}
```

**Response:**
```json
{
  "id": 1,
  "title": "Full-Stack Web Development Mastery",
  "description": "A comprehensive path...",
  "experience_level": "beginner",
  "time_commitment": "10-20 hours/week",
  "created_at": "2024-01-22T12:00:00",
  "milestones": [
    {
      "id": 1,
      "title": "HTML & CSS Fundamentals",
      "description": "Learn the building blocks...",
      "order": 0,
      "estimated_hours": 15,
      "resources": ["MDN Web Docs", "freeCodeCamp"],
      "completed": false,
      "completed_at": null
    }
  ]
}
```

#### `GET /api/paths`
Get all learning paths

#### `GET /api/paths/{path_id}`
Get a specific learning path

#### `PATCH /api/milestones/{milestone_id}`
Update milestone completion status

**Request Body:**
```json
{
  "completed": true
}
```

#### `DELETE /api/paths/{path_id}`
Delete a learning path

### Interactive API Docs
Visit `http://localhost:8000/docs` for Swagger UI documentation

## 🎨 Design Decisions

### Product Decisions

**Focus on Learning Specifically**
- Generic task breakdown tools exist, but learning has unique needs
- Progressive structure matters (prerequisites, building blocks)
- Resource recommendations are crucial for self-learners

**AI Prompt Engineering**
- Structured JSON output ensures consistency
- Explicit instructions for progressive ordering
- Request for "why" explanations to add educational value

**Progress Tracking Over Collaboration**
- MVP focuses on individual learners
- Completion tracking provides motivation
- Visual progress creates sense of achievement

### Technical Decisions

**Monorepo Structure**
- Single repository for simplicity
- Easy to run and deploy
- Clear separation of concerns

**SQLite for MVP**
- Zero configuration
- File-based, portable
- Easy migration path to PostgreSQL

**Vanilla CSS Over Tailwind**
- Full design control
- Demonstrates CSS expertise
- Faster for custom design system
- No build complexity

**React State Over Redux**
- Simple state needs (current path, view)
- useState sufficient for MVP
- Reduces complexity and bundle size

**GPT-4 Mini for Cost Efficiency**
- Sufficient quality for structured output
- Faster response times
- Lower API costs

### UX Decisions

**Dark Theme**
- Modern, professional aesthetic
- Reduces eye strain for extended use
- Aligns with developer tools aesthetic

**Glassmorphism**
- Creates depth and visual interest
- Modern design trend
- Enhances premium feel

**Timeline Visualization**
- Intuitive representation of learning journey
- Shows progression clearly
- Familiar mental model

**Expandable Milestones**
- Reduces initial cognitive load
- Progressive disclosure of information
- Keeps interface clean

## 🔮 Future Improvements (v2)

### Features
- **User Authentication**: Personal accounts and saved paths
- **Path Sharing**: Share learning paths with others
- **Community Resources**: User-submitted resource recommendations
- **AI Refinement**: "Regenerate this milestone" for fine-tuning
- **Calendar Integration**: Schedule milestones with deadlines
- **Learning Analytics**: Track time spent, completion rates
- **Skill Trees**: Visualize prerequisite relationships
- **Mobile App**: Native iOS/Android apps
- **Collaborative Paths**: Team learning with shared progress

### Technical Improvements
- **Caching**: Cache AI responses for common goals
- **Rate Limiting**: Prevent API abuse
- **PostgreSQL**: Production-ready database
- **Docker**: Containerized deployment
- **CI/CD**: Automated testing and deployment
- **Monitoring**: Error tracking and analytics
- **A/B Testing**: Optimize prompts and UX

## Time Breakdown

**Total Time: ~6 hours**

- **Planning & Architecture** (45 min)
  - Problem definition and solution design
  - Tech stack selection
  - Architecture planning

- **Backend Development** (1.5 hours)
  - FastAPI setup and configuration
  - Database models and migrations
  - AI service integration
  - REST API endpoints
  - Testing with Swagger UI

- **Frontend Development** (2.5 hours)
  - Vite + React setup
  - Design system and CSS
  - Landing page component
  - Learning path component
  - State management and routing
  - Responsive design

- **Integration & Testing** (45 min)
  - Connect frontend to backend
  - Test full user flows
  - Fix bugs and edge cases
  - Polish animations and UX

- **Documentation** (45 min)
  - README creation
  - Code comments
  - API documentation
  - Architecture diagrams

### Tradeoffs Made

**Chose to prioritize:**
- ✅ Polished, complete user experience
- ✅ Strong visual design and animations
- ✅ Comprehensive documentation
- ✅ Clean, maintainable code

**Deferred for v2:**
- ⏭ User authentication
- ⏭ Path history/browsing
- ⏭ Advanced AI features (refinement, chat)
- ⏭ Deployment to production
- ⏭ Automated tests

## License

MIT License - feel free to use this project for learning or inspiration!

## Author

**Max**
- GitHub: [@GibbHubb](https://github.com/GibbHubb)
