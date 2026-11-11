# Connectly – Real-Time Messaging Platform

A real-time messaging application inspired by modern chat platforms, built using **Python, FastAPI, WebSockets, and MongoDB**. Connectly enables users to communicate through private conversations and group chats, share files, and view live user activity.

## Features

- **Simple User Login:** Join the application using a username.
- **One-to-One Messaging:** Send private messages between users.
- **Group Conversations:** Create groups and communicate with multiple users.
- **Real-Time Messaging:** Exchange messages instantly using WebSockets.
- **Message Persistence:** Store and retrieve chat history using MongoDB.
- **Online/Offline Status:** View users' current availability.
- **Typing Indicators:** See when another user is typing.
- **File & Image Sharing:** Upload and share files and images within conversations.
- **Responsive Interface:** Clean frontend built with HTML, CSS, and JavaScript.

---

## Technology Stack

| Category | Technologies |
|---|---|
| Backend | Python, FastAPI |
| Database | MongoDB |
| Database Driver | Motor |
| Real-Time Communication | WebSockets |
| File Storage | MongoDB GridFS |
| Frontend | HTML, CSS, JavaScript |
| API Documentation | FastAPI / Swagger UI |
| Deployment | Render |
| Version Control | Git, GitHub |

---

## Project Architecture

```text
Connectly/
│
├── backend/
│   ├── app.py
│   ├── config/
│   │   ├── settings.py
│   │   └── logging.py
│   │
│   ├── db/
│   │   └── database.py
│   │
│   ├── models/
│   │   └── schemas.py
│   │
│   ├── services/
│   │   ├── messages.py
│   │   └── ws_manager.py
│   │
│   ├── utils/
│   │   └── helpers.py
│   │
│   └── routers/
│       ├── users.py
│       ├── chats.py
│       ├── files.py
│       └── websocket.py
│
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
│
├── .env.example
├── .gitignore
├── render.yaml
├── requirements.txt
├── runtime.txt
└── README.md
```

---

## Getting Started

Follow these instructions to run Connectly locally.

### Prerequisites

Make sure you have installed:

- Python 3.10 or later (use the version supported by the project dependencies).
- MongoDB Atlas account or a local MongoDB installation.
- Git.

### 1. Clone the Repository

```bash
git clone https://github.com/Allupriya/Connectly.git
cd Connectly
```

### 2. Create a Virtual Environment

**Windows (PowerShell):**

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file in the project root by copying the example file.

```powershell
Copy-Item .env.example .env
```

Update `.env` with your MongoDB connection details:

```env
MONGODB_URL=mongodb+srv://username:password@cluster-url/
MONGODB_DB=chattingsystem
```

Replace the example connection string with your actual MongoDB connection URL.

**Important:** Never commit your `.env` file or expose database credentials in a public repository.

### 5. Run the Application

Start the FastAPI development server:

```bash
uvicorn backend.app:app --reload
```

Open the application in your browser:

**Application:** http://localhost:8000

**API Documentation:** http://localhost:8000/docs

---

## How to Use

1. Open Connectly in your browser.
2. Enter a username to join the application.
3. Start a direct conversation with another registered user.
4. Create a group and select its members.
5. Exchange messages in real time.
6. Share files and images within conversations.
7. View online status and typing indicators.

For testing, open the application in two different browsers or use a normal and an incognito window with different usernames.

---

## Database

Connectly uses MongoDB for persistent storage.

The application uses MongoDB collections to manage user accounts, conversations, messages, and related data.

MongoDB GridFS is used to store uploaded files and images.

Database configuration is managed through environment variables rather than hardcoded credentials.

---

## Deployment

Connectly includes a `render.yaml` configuration for deployment on [Render](https://render.com/).

General deployment steps:

1. Push the project to GitHub.
2. Sign in to Render.
3. Select **New → Blueprint**.
4. Connect your GitHub repository.
5. Configure the required environment variables, including `MONGODB_URL` and `MONGODB_DB`.
6. Deploy the application.

The deployment command is:

```bash
pip install -r requirements.txt
uvicorn backend.app:app --host 0.0.0.0 --port $PORT
```

Make sure your MongoDB Atlas network access settings allow connections from your deployment environment.

---

## Security Considerations

- The current login implementation uses username-only authentication without passwords.
- This version is intended for demonstration and learning purposes.
- Password-based authentication and stronger session management should be implemented before handling sensitive conversations.
- Keep database credentials and private environment variables outside the public repository.

---

## Future Improvements

- Secure authentication using JWT and password hashing.
- Message read receipts and delivery status.
- Message editing and deletion.
- Push notifications for new messages.
- Improved mobile responsiveness.
- Message search and filtering.
- User profile management.

---

## Author

**Allupriya**

GitHub: [@Allupriya](https://github.com/Allupriya)

Project: [Connectly – Real-Time Messaging Platform](https://github.com/Allupriya/Connectly)
---
*Connectly – Bringing conversations closer, one message at a time.*
