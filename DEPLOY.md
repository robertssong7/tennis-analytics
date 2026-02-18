# Deploying TennisIQ to GitHub Pages

You have chosen the **Static Export** option, which means your entire site is pre-built into the `public/` folder. You can host this for free on GitHub Pages.

## Prerequisites
- A GitHub account
- Git installed on your computer

## Steps

1. **Initialize a Git repository** (if you haven't already):
   ```bash
   cd C:\Users\Robert\.gemini\antigravity\scratch\tennis-analytics
   git init
   git add .
   git commit -m "Initial commit of TennisIQ"
   ```

2. **Create a new repository on GitHub:**
   - Go to [github.com/new](https://github.com/new)
   - Name it `tennis-analytics` (or whatever you like)
   - Make it **Public** (required for free GitHub Pages unless you have Pro)
   - Click **Create repository**

3. **Push your code:**
   ```bash
   # Replace <YOUR_USERNAME> with your actual GitHub username
   git remote add origin https://github.com/<YOUR_USERNAME>/tennis-analytics.git
   git branch -M main
   git push -u origin main
   ```

4. **Enable GitHub Pages:**
   - Go to your repository settings on GitHub
   - Click **Pages** in the left sidebar
   - Under **Build and deployment** > **Source**, select **Deploy from a branch**
   - Under **Branch**, select `main` and select the `/public` folder (if available) 
     - *Note: If `/public` isn't an option in the dropdown, you can push just the public folder to a separate branch, or use a custom workflow. The easiest way without config is to use the `gh-pages` package.*

### Alternative: Use `gh-pages` package (Easiest)

If you don't want to mess with settings:

1. Run:
   ```bash
   npm install --save-dev gh-pages
   ```

2. Add this script to `package.json`:
   ```json
   "scripts": {
     "deploy": "gh-pages -d public"
   }
   ```

3. Run:
   ```bash
   npm run deploy
   ```
   
   This will automatically create a `gh-pages` branch with *only* the contents of your `public/` folder and publish it. Your site will be live at `https://<user>.github.io/tennis-analytics/`.

## Updating Data

To update the data in the future (e.g. after adding more matches to the database):

1. Run the generation script:
   ```bash
   node generate-static.js
   ```
2. Deploy again:
   ```bash
   npm run deploy
   ```
