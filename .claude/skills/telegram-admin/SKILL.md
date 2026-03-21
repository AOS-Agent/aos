# Skill: Telegram Admin

Comprehensive reference for managing Telegram bots, groups, and forum topics via both the Bot API and the desktop app (via Steer).

## Bot API Methods — Forum Topics

Bot must be admin with `can_manage_topics` permission.

| Method | Params | Returns |
|--------|--------|---------|
| `createForumTopic` | `chat_id`, `name`, `icon_color?`, `icon_custom_emoji_id?` | `ForumTopic` with `message_thread_id` |
| `editForumTopic` | `chat_id`, `message_thread_id`, `name?`, `icon_custom_emoji_id?` | `True` |
| `closeForumTopic` | `chat_id`, `message_thread_id` | `True` |
| `reopenForumTopic` | `chat_id`, `message_thread_id` | `True` |
| `deleteForumTopic` | `chat_id`, `message_thread_id` | `True` |

Icon colors (limited to 6): `0x6FB9F0`, `0xFFD67E`, `0xCB86DB`, `0x8EEE98`, `0xFF93B2`, `0xFB6F5F`

## Bot API Methods — Group Management

| Method | Purpose |
|--------|---------|
| `createChatInviteLink` | Create invite link (with expiry, limits) |
| `getChatMember` | Get member info |
| `getChatMemberCount` | Get total members |
| `banChatMember` / `unbanChatMember` | Manage members |
| `promoteChatMember` | Grant admin rights |
| `getChat` | Get chat info (title, description, etc.) |

## Bot API Methods — Self Configuration

| Method | Purpose |
|--------|---------|
| `setMyCommands` | Set command menu (with scope & language) |
| `setMyDescription` | Set description shown in empty chat |
| `setMyShortDescription` | Set short profile description |
| `setMyName` | Change display name |
| `setMyProfilePhoto` | Upload profile photo |

## BotFather Commands (via Steer)

These require interacting with @BotFather in the Telegram desktop app:

| Command | What it does |
|---------|-------------|
| `/newbot` | Create a new bot (prompts for name + username) |
| `/mybots` | List your bots |
| `/setdescription` | Set bot description |
| `/setabouttext` | Set About text |
| `/setuserpic` | Set profile picture |
| `/setcommands` | Set command list |
| `/setprivacy` | Toggle group privacy mode |
| `/setinline` | Enable inline mode |
| `/deletebot` | Delete a bot |
| `/token` | Generate new token |

## Creating a Bot via Steer

```
1. steer apps launch "Telegram"
2. Navigate to @BotFather chat (search or recent)
3. steer type "/newbot" → steer keyboard enter
4. Wait for BotFather response → read via steer see --app Telegram
5. steer type "Bot Display Name" → enter
6. steer type "bot_username_bot" → enter
7. Read response → extract token (format: 1234567890:AAAAAAA...)
8. Store: bin/agent-secret set NEW_BOT_TOKEN <token>
```

## Creating a Group with Forums (via Steer)

```
1. Open Telegram → click compose (pencil icon top-right)
2. Select "New Group"
3. Add the bot as a member (search by @username)
4. Set group name → Create
5. Open group → click group name (header) → Settings
6. Scroll to "Topics" → enable toggle
7. Go back → promote bot to admin with "Manage Topics" permission
```

## Sending Messages to Forum Topics

When sending via Bot API, include `message_thread_id`:
```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -d "chat_id=<GROUP_ID>&message_thread_id=<THREAD_ID>&text=Hello"
```

## python-telegram-bot Library

The bridge uses `python-telegram-bot>=21.0`. Key classes:
- `Application.builder().token(TOKEN).build()` — create bot
- `Bot.create_forum_topic(chat_id, name)` → returns `ForumTopic`
- `Message.message_thread_id` — identifies which topic a message belongs to
- `CallbackQueryHandler` — handles inline button presses
- `filters.VOICE`, `filters.PHOTO`, `filters.TEXT` — message type filters
