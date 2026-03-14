# 🌟 Riya Stories Bot v10

A high-performance, feature-rich Telegram bot designed for indexing, searching, and managing stories across multiple channels. Built with a focus on speed, user experience, and robust admin management.

## ✨ Key Features

### 🔍 Search & Discovery
*   **AI Fuzzy Search**: Advanced search powered by `rapidfuzz` that handles typos and complex queries.
*   **Trending Stories**: Real-time tracking of the most popular searches.
*   **Categorized Browsing**: Automated category extraction from story posts for easy navigation.
*   **Recent Series**: Quickly view the 10 most recently added stories.

### 🍱 User Experience
*   **Premium Unicode UI**: A clean, modern interface using custom Unicode symbols instead of standard emojis.
*   **Favorites & Saved Stories**: Users can save their favorite stories for quick access directly in-bot.
*   **Language System**: Full support for both **English** and **Hindi** interfaces.
*   **Inline Navigation**: Responsive inline keyboard buttons for searching, pagination, and menu management.
*   **Notification System**: Users can subscribe to get alerts when new stories are added.

### 🛡️ Admin & Management
*   **Dynamic Channel Management**: Add/Remove source channels directly via the bot UI.
*   **Format Learning System**: Teach the bot new post formats simply by forwarding a sample post—no regex required.
*   **Link Verification**: Integrated broken link reporter with an admin-driven voting/verification system.
*   **Maintenance Mode**: Easily enable bot-wide maintenance with custom durations.
*   **Detailed Analytics**: Real-time system stats, user search history, and trending data.
*   **Story Request System**: Manage and respond to user story requests with automated notifications.

---

## 🚀 Setup & Deployment

### 📋 Prerequisites
*   Python 3.9+
*   Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
*   Telegram API ID & Hash (from [my.telegram.org](https://my.telegram.org))

### 🛠️ Local Setup

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/jeeteshmeena/riya-stories-bot.git
    cd riya-stories-bot
    ```

2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure Environment**:
    *   Copy `.env.example` to `.env`.
    *   Fill in your `BOT_TOKEN`, `API_ID`, `API_HASH`, and `ADMIN_ID`.

4.  **Generate Session String**:
    *   This is required for the bot to scan channels using the Telethon client.
    ```bash
    python generate_session.py
    ```
    *   Copy the generated `SESSION_STRING` to your `.env` file.

5.  **Run the Bot**:
    ```bash
    python stories_bot.py
    ```

---

## ⚙️ Configuration (.env)

| Variable | Description |
| :--- | :--- |
| `BOT_TOKEN` | Token provided by @BotFather. |
| `API_ID` / `API_HASH` | Your Telegram API credentials. |
| `SESSION_STRING` | String session from Telethon. |
| `ADMIN_ID` / `OWNER_ID` | Telegram User IDs authorized for admin commands. |
| `CHANNEL_ID` | The primary channel to be scanned. |
| `REQUEST_GROUP` | Group where user requests will be forwarded. |
| `LOG_CHANNEL` | Channel for internal bot logs. |
| `AUTO_SCAN` | Set to `true` to enable automated channel re-scanning. |

---

## 📜 Deployment

For detailed production deployment instructions (including Linux/systemd setup), please refer to the [Deployment Guide](DEPLOYMENT_GUIDE.md).

### Quick Deploy (VPS)
```bash
chmod +x deploy/install-vps.sh deploy/run.sh
./deploy/install-vps.sh
# Follow instructions to setup systemd service
```

---

## 🤝 Support & Contribution
Developed and maintained by **@MeJeetX**.

Feel free to report issues or suggest new features via the request system!
