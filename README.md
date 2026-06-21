# 🤖 Telegram AdBot

Ein Telegram Werbe-Bot mit Credits-System.

## 🚀 Setup

### 1. Bot erstellen
1. Öffne [@BotFather](https://t.me/botfather) auf Telegram
2. Schreibe `/newbot` und folge den Anweisungen
3. Kopiere den **Bot Token**

### 2. Deine Admin-ID herausfinden
1. Schreibe [@userinfobot](https://t.me/userinfobot) eine Nachricht
2. Notiere deine **User-ID**

### 3. Konfiguration
Kopiere `.env.example` zu `.env` und fülle aus:
```
BOT_TOKEN=dein_token_hier
ADMIN_IDS=deine_user_id
```

### 4. Lokal testen
```bash
pip install -r requirements.txt
python bot.py
```

## ☁️ Gratis Hosting

### Option 1: Railway.app (EMPFOHLEN ⭐)
1. https://railway.app → Account erstellen
2. "New Project" → "Deploy from GitHub"
3. Repo hochladen, Environment Variables setzen
4. Läuft 24/7 kostenlos (500h/Monat gratis)

### Option 2: Render.com
1. https://render.com → Account erstellen  
2. "New" → "Background Worker"
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python bot.py`
5. Environment Variables: BOT_TOKEN, ADMIN_IDS

### Option 3: Koyeb.com
1. https://koyeb.com → Account erstellen
2. "Create App" → GitHub verbinden
3. Gratis Tier verfügbar

## 👑 Admin-Befehle

| Befehl | Beschreibung |
|--------|-------------|
| `/give_credits 123456 500` | User Credits geben |
| `/add_group -1001234567890 Meine Gruppe` | Gruppe hinzufügen |
| `/remove_group -1001234567890` | Gruppe entfernen |
| `/list_groups` | Alle Gruppen anzeigen |
| `/stats` | Bot-Statistiken |
| `/getid` | ID des aktuellen Chats |

## 📢 Gruppen zum Netzwerk hinzufügen

1. Bot in die Gruppe einladen
2. Bot zum **Admin** machen (braucht "Nachrichten senden" Berechtigung)
3. Schreibe `/getid` in der Gruppe → kopiere die ID
4. Als Admin: `/add_group <ID> <Gruppenname>`

## 💰 Credit-Pakete anpassen

In `bot.py` → `CREDIT_PACKAGES` Dictionary bearbeiten.
In `bot.py` → `CREDITS_PER_GROUP` für Kosten pro Gruppe ändern.
