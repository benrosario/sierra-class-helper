# Railway Deployment Guide - Sierra Class Helper

This guide will walk you through deploying both the API server and Discord bot to Railway.

## Prerequisites

- [x] Railway account (sign up at https://railway.app)
- [x] GitHub account with this repository
- [x] OpenAI API key (from https://platform.openai.com/api-keys)
- [x] Discord bot token (from https://discord.com/developers/applications)

## Estimated Monthly Cost

- **Two Railway Services**: ~$10-13/month (usage-based)
- **OpenAI API**: ~$1-5/month (depending on usage)
- **Total**: ~$11-18/month

## Deployment Steps

### Step 1: Push Your Code to GitHub

Make sure all required files are committed:

```bash
# Check git status
git status

# Add deployment files
git add railway.toml .railwayignore Procfile .env.example DEPLOYMENT.md

# Make sure these critical files are committed:
git add courses.index id_to_course.json course_data/

# Commit
git commit -m "Add Railway deployment configuration"

# Push to GitHub
git push origin main
```

### Step 2: Create Railway Project

1. Go to https://railway.app and sign in
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Authorize Railway to access your GitHub account
5. Select the `class-gpt` repository

### Step 3: Create API Service

Railway should automatically detect your `railway.toml` and create both services. If not:

1. Click **"+ New"** → **"Service"**
2. Select your GitHub repository
3. Name it: `api`
4. Railway will auto-detect Python and install dependencies

### Step 4: Configure API Environment Variables

1. Click on the **API service**
2. Go to **"Variables"** tab
3. Add the following variables:

```
OPENAI_API_KEY=your-actual-openai-api-key
PORT=8000
ENVIRONMENT=production
```

4. Click **"Deploy"**

### Step 5: Wait for API to Deploy

- Watch the deployment logs
- Wait for "Deployment successful" message
- Note the public URL (e.g., `https://api-production-xxxx.up.railway.app`)

### Step 6: Create Discord Bot Service

1. Click **"+ New"** → **"Service"**
2. Select your GitHub repository again
3. Name it: `bot`
4. Railway will auto-detect Python

### Step 7: Configure Bot Environment Variables

1. Click on the **Bot service**
2. Go to **"Variables"** tab
3. Add the following variables:

```
DISCORD_BOT_TOKEN=your-actual-discord-bot-token
OPENAI_API_KEY=your-actual-openai-api-key
API_URL=https://api-production-xxxx.up.railway.app
```

**Important**: Replace `https://api-production-xxxx.up.railway.app` with your actual API service URL from Step 5.

4. Click **"Deploy"**

### Step 8: Verify Deployment

#### Check API Health

Visit your API URL in a browser:
- `https://your-api-url.railway.app/` should return `{"status": "healthy"}`
- `https://your-api-url.railway.app/health` should return detailed health info

#### Check Discord Bot

1. Go to Discord
2. Invite your bot to a server (if not already done)
3. Try the `/ask` command: `/ask What computer science classes are available?`
4. You should get a response from the bot

### Step 9: Monitor Your Services

Railway Dashboard shows:
- **CPU Usage**
- **Memory Usage**
- **Deployment Logs**
- **Estimated Monthly Cost**

## Troubleshooting

### Bot not responding in Discord

**Check:**
1. Bot service logs in Railway for errors
2. `DISCORD_BOT_TOKEN` is correct
3. `API_URL` points to your Railway API service
4. Bot has proper permissions in Discord server

**View Logs:**
```
Railway Dashboard → Bot Service → Deployments → View Logs
```

### API returning 500 errors

**Check:**
1. API service logs for errors
2. `OPENAI_API_KEY` is valid
3. Required files exist: `courses.index`, `id_to_course.json`, `course_data/`

**View Logs:**
```
Railway Dashboard → API Service → Deployments → View Logs
```

### "File not found" errors

**Problem**: Data files not in repository

**Solution:**
```bash
# Ensure these files are NOT in .gitignore
git add courses.index id_to_course.json course_data/
git commit -m "Add course data files for deployment"
git push origin main

# Railway will auto-redeploy
```

### High costs / unexpected billing

**Check:**
1. Railway Dashboard → Billing → Usage
2. Look for unexpected traffic spikes
3. Review OpenAI API usage at https://platform.openai.com/usage

**Optimize:**
- Set spending limits in Railway
- Monitor OpenAI API usage
- Consider caching responses for common queries

## Updating Your Deployment

Railway auto-deploys on every git push to `main`:

```bash
# Make your changes
git add .
git commit -m "Update course data"
git push origin main

# Railway will automatically:
# 1. Pull latest code
# 2. Rebuild both services
# 3. Deploy with zero downtime
```

## Manual Redeploy

If you need to redeploy without code changes:

1. Railway Dashboard → Service → Deployments
2. Click **"..."** on latest deployment
3. Select **"Redeploy"**

## Updating Course Data

When you update course data locally:

```bash
# 1. Scrape new data
python -m src.scraper.cli

# 2. Rebuild embeddings
python -m src.embeddings.fast

# 3. Commit and push
git add courses.index id_to_course.json course_data/
git commit -m "Update course catalog for [Semester] [Year]"
git push origin main

# Railway will auto-deploy the updated data
```

## Cost Optimization Tips

1. **Monitor usage weekly**: Railway Dashboard → Billing
2. **Set spending limits**: Railway Settings → Billing → Spending Limit
3. **Use Railway's $5 trial**: Test everything before committing
4. **Track OpenAI costs**: https://platform.openai.com/usage
5. **Consider caching**: Add Redis for frequently asked questions (optional)

## Railway CLI (Optional)

Install for advanced management:

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login
railway login

# Link to your project
railway link

# View logs
railway logs

# Run commands in Railway environment
railway run python api_server.py
```

## Alternative: Running Locally

If you need to test before deploying:

```bash
# 1. Copy environment template
cp .env.example .env

# 2. Fill in your actual keys in .env
nano .env

# 3. Start both services
./start_local.sh

# Or manually in separate terminals:
# Terminal 1: python api_server.py
# Terminal 2: python discord_bot.py
```

## Support

- **Railway Docs**: https://docs.railway.app
- **Railway Discord**: https://discord.gg/railway
- **OpenAI Support**: https://help.openai.com
- **Discord.py Docs**: https://discordpy.readthedocs.io

## Architecture Overview

```
┌─────────────────────────────────────────────────┐
│                  Railway Cloud                   │
│                                                  │
│  ┌──────────────────┐      ┌──────────────────┐ │
│  │   API Service    │      │   Bot Service    │ │
│  │                  │      │                  │ │
│  │  api_server.py   │◄─────│ discord_bot.py   │ │
│  │                  │ HTTP │                  │ │
│  │  FastAPI + FAISS │      │  discord.py      │ │
│  │                  │      │                  │ │
│  └────────┬─────────┘      └────────┬─────────┘ │
│           │                         │           │
└───────────┼─────────────────────────┼───────────┘
            │                         │
            │                         │
    ┌───────▼─────────┐       ┌──────▼──────┐
    │  OpenAI API     │       │   Discord   │
    │  (Embeddings +  │       │   Servers   │
    │   GPT-4o-mini)  │       │             │
    └─────────────────┘       └─────────────┘
```

## Automated Course Updates (Cron Job)

Sierra Class Helper includes an automated course updater that runs every hour from 8am-8pm PST to keep enrollment data fresh during registration periods.

### How It Works

The `course-updater` service:
1. **Scrapes** latest course data from Sierra College
2. **Rebuilds embeddings** using fast incremental method
3. **Updates** the live data automatically
4. **Runs** hourly during peak registration times (8am-8pm PST)

### Setting Up the Cron Service

The cron configuration is already in `railway.toml`. Railway should automatically create the service when you deploy.

**If Railway doesn't auto-create it:**

1. In Railway Dashboard, click **"+ New"** → **"Cron Job"**
2. Select your repository
3. Name it: `course-updater`
4. Set these configurations:
   - **Start Command**: `python -m src.scraper.cli && python -m src.embeddings.fast`
   - **Schedule**: `0 15-23,0-3 * * *` (hourly from 8am-8pm PDT in UTC)
   - **Build Command**: `pip install -r requirements.txt && playwright install chromium`
5. Add environment variables:
   ```
   OPENAI_API_KEY=your-key
   ENVIRONMENT=production
   ```
6. Click **"Deploy"**

### Monitoring Cron Jobs

**View Logs:**
1. Railway Dashboard → Course Updater service
2. Click **"Deployments"** tab
3. Select a run to see logs
4. Look for: `Course update process completed` + `Status: SUCCESS ✓`

**Check Schedule:**
- Cron runs at :00 of each hour from 8am-8pm PST
- That's 13 runs per day (8am, 9am, ..., 8pm)
- Each run takes ~2-5 minutes

**Manual Trigger:**
If you need to update courses outside the schedule:
1. Railway Dashboard → Course Updater
2. Click **"..."** menu → **"Trigger Deploy"**
3. Or run locally: `python -m src.scraper.cli && python -m src.embeddings.fast`

### Cost Estimate

- **Cron Job Service**: ~$1-3/month
- Runs: 13 times/day × ~3 min/run = 39 min/day
- Monthly: ~20 hours of compute time
- Railway charges per second of usage

### Troubleshooting Cron

**Cron not running:**
- Check Railway Dashboard → Course Updater → Settings → Cron
- Verify schedule is `0 15-23,0-3 * * *` (UTC times for 8am-8pm PDT)

**Scraping fails:**
- Check if Sierra College website structure changed
- View logs: `src.scraper.cli` errors
- May need to update `src/scraper/` selectors

**Embeddings fail:**
- Check OPENAI_API_KEY is set in cron service
- View logs: `src.embeddings.fast` errors
- Ensure course data files exist

**Data not updating:**
- Check cron logs show SUCCESS
- Verify files are being committed (if using GitHub Actions) OR updated in Railway volume
- Restart API service if needed to reload data

### Adjusting the Schedule

Edit `railway.toml` and change the cron schedule:

```toml
[services.cron]
schedule = "0 15-23,0-3 * * *"  # Current: hourly 8am-8pm PDT (in UTC)
# schedule = "0 15-23,0-5 * * *"  # Alternative: 8am-10pm PDT
# schedule = "0 */2 * * *"         # Alternative: every 2 hours (24/7)
# schedule = "0 * * * *"           # Alternative: every hour (24/7)
```

Then git push - Railway will update the schedule automatically.

## Next Steps After Deployment

1. **Invite bot to your Discord server**
2. **Test all slash commands** (`/ask`, `/search`, `/clear`)
3. **Verify cron job** is running (check Railway dashboard)
4. **Monitor costs** for first week
5. **Set up alerts** for usage spikes (Railway Settings)
6. **Consider adding**:
   - Error tracking (Sentry)
   - Analytics (Mixpanel, PostHog)
   - Caching layer (Redis)

## Success Checklist

- [ ] API service deployed and healthy
- [ ] Bot service deployed and online
- [ ] Cron job service created and scheduled
- [ ] Bot responds to `/ask` in Discord
- [ ] `/search` command works
- [ ] Health endpoint accessible
- [ ] Environment variables set correctly
- [ ] Monitoring costs in Railway dashboard
- [ ] Auto-deploy enabled on git push
- [ ] Cron job running successfully (check logs)

---

**Estimated deployment time**: 15-20 minutes

**Questions?** Check Railway logs first, then consult Discord.py and FastAPI documentation.
