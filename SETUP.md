# Quick Setup Guide

## Current Status
✅ Backend is running on http://localhost:8000
✅ Code pushed to GitHub: https://github.com/GibbHubb/ai-learning-path-generator

## Frontend Setup (PowerShell Execution Policy Workaround)

Since PowerShell has script execution disabled, use one of these methods:

### Option 1: Use CMD instead
```cmd
cd frontend
npm install
npm run dev
```

### Option 2: Temporarily allow PowerShell scripts (Run as Administrator)
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
cd frontend
npm install
npm run dev
```

### Option 3: Use npx directly
```powershell
cd frontend
npx --yes vite
```

## Testing the Application

1. **Backend** (Already Running ✅)
   - URL: http://localhost:8000
   - API Docs: http://localhost:8000/docs
   - Health Check: http://localhost:8000/health

2. **Frontend** (After npm install)
   - Run: `npm run dev`
   - URL: http://localhost:5173

3. **Test Flow**
   - Open http://localhost:5173
   - Enter a learning goal (e.g., "Learn Python Programming")
   - Select experience level and time commitment
   - Click "Generate Learning Path"
   - View the generated path with milestones
   - Click milestones to expand details
   - Mark milestones as complete

## Next Steps

1. Install frontend dependencies (use one of the methods above)
2. Start frontend dev server
3. Test the full application
4. Make additional commits as needed
5. Create demo video
6. Polish and finalize

## Git Commands for Future Commits

```bash
git add .
git commit -m "Your commit message"
git push
```
