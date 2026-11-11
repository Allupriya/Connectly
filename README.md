# ChatSystem

A Telegram-style chat app built with **FastAPI** + **WebSockets** and **MongoDB**.

## Features

- Simple login — just pick a username (auto-creates the user, no password)
- **1-on-1 direct messages** and **group chats**
- **Real-time messaging** over WebSockets
- **Persistent history** stored in MongoDB
- **Online / offline presence** and **typing indicators**
- **File & image sharing**
- Clean vanilla HTML/CSS/JS frontend served by the backend

## Project structure

```
chattingsystem/
├── backend/
│   ├── app.py               # FastAPI app: wiring, lifespan, exception handlers
│   ├── config/              # Configuration
│   │   ├── settings.py      # Env-driven settings (URLs, collection names...)
│   │   └── logging.py       # Central logging setup
│   ├── db/
│   │   └── database.py      # MongoDB (Motor) connection, collections, GridFS
│   ├── models/
│   │   └── schemas.py       # Pydantic models + serialization helpers
│   ├── services/            # Business logic
│   │   ├── messages.py      # Message storage
│   │   └── ws_manager.py    # Live WebSocket connection tracking
│   ├── utils/
│   │   └── helpers.py       # Id parsing + document shaping helpers
│   └── routers/             # HTTP + WebSocket endpoints
│       ├── users.py
│       ├── chats.py
│       ├── files.py
│       └── websocket.py
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── render.yaml              # Render deploy blueprint
├── requirements.txt
└── .env.example
```

All environment-specific and sensitive values (MongoDB URL/credentials,
database name, collection and GridFS bucket names, log level) are read from the
`.env` file via `backend/config/settings.py` — nothing is hard-coded.

## Setup

### 1. Create a virtual environment and install dependencies

Windows (PowerShell):

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure MongoDB

Copy `.env.example` to `.env` and set your MongoDB connection string:

```
MONGODB_URL=mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
MONGODB_DB=chattingsystem
```

You can use a free [MongoDB Atlas](https://www.mongodb.com/atlas) cluster or a local `mongodb://localhost:27017`.

### 3. Run the server

```powershell
uvicorn backend.app:app --reload
```

Open http://localhost:8000 in your browser.

## Try it out

1. Open the site in two different browsers (or one normal + one incognito window).
2. Log in with a different username in each.
3. Click **+ New chat** to start a DM, or **+ New group** to create a room.
4. Send messages, share files, and watch typing indicators and online status update live.

## Deploy to Render

This repo includes a `render.yaml` blueprint, so deploying is one step:

1. Push the repo to GitHub.
2. In Render, click **New +** → **Blueprint** and select the repo.
3. When prompted, fill in the secret env vars (they are **not** stored in git):
   - `MONGODB_URL` (and optionally `MONGODB_USERNAME` / `MONGODB_PASSWORD`)
4. Deploy. Render runs:

```
pip install -r requirements.txt
uvicorn backend.app:app --host 0.0.0.0 --port $PORT
```

Uploads are stored in MongoDB (GridFS), so the app works fine on Render's
ephemeral filesystem — nothing is written to local disk.

## Notes

- Auth is intentionally minimal (username only) for easy testing. Add passwords/JWT before any real deployment.
- Uploaded files are stored in MongoDB using GridFS (the `uploads` bucket) and streamed back via `GET /uploads/{file_id}`. Nothing is written to local disk, so the app works on hosts with an ephemeral filesystem (e.g. Render).
