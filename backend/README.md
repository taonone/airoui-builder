# Airoui Backend Deployment Instructions

## 1. Prerequisites
- Node.js installed
- Cloudflare Account

## 2. Setup
Open a terminal in this `backend` folder:
```bash
npm install
```

## 3. Database Setup (D1)
Run the following commands to create and configure your database:
```bash
# Login to Cloudflare
npx wrangler login

# Create D1 Database (Copy the database_id from the output!)
npx wrangler d1 create airoui-db

# Update wrangler.toml
# Open wrangler.toml and replace "REPLACE_WITH_YOUR_DB_ID" with the ID you just got.

# Initialize Schema
npx wrangler d1 execute airoui-db --file=./schema.sql
```

## 4. Deploy
```bash
npx wrangler deploy
```

## 5. Connect Frontend
Copy your Worker URL (e.g., `https://airoui-backend.yourname.workers.dev`) and update `apiBase` in `CloudManager.js` (inside index.html).
