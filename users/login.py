import asyncio
import os
import sys
import json

from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
try:
    # Try importing the attribute for animated media (GIF)
    from telethon.tl.types import DocumentAttributeAnimation, DocumentAttributeVideo
except ImportError:
    # Fallback for certain Telethon versions
    from telethon.tl.types import DocumentAttributeAnimated as DocumentAttributeAnimation, DocumentAttributeVideo

from dotenv import load_dotenv
import qrcode

# Load credentials from the .env file
load_dotenv()
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')
owner_chat_id_str = os.getenv('OWNER_CHAT_ID')

if not api_id or not api_hash:
    print("Please ensure that API_ID and API_HASH are set in your .env file.")
    sys.exit(1)
if not owner_chat_id_str:
    print("Please ensure that OWNER_CHAT_ID is set in your .env file.")
    sys.exit(1)

try:
    owner_chat_id = int(owner_chat_id_str)
except ValueError:
    print("Invalid OWNER_CHAT_ID. It should be an integer.")
    sys.exit(1)

# File used to store the blocked chat IDs
BLOCKED_CHATS_FILE = "blocked_chats.json"

def load_blocked_chat_ids():
    """
    Loads the list of blocked chat IDs from the JSON file.
    If the file does not exist or is invalid, creates an empty JSON array.
    """
    if os.path.exists(BLOCKED_CHATS_FILE):
        try:
            with open(BLOCKED_CHATS_FILE, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            print(f"Error reading {BLOCKED_CHATS_FILE}: {e}")
    # Create an empty file if it doesn't exist or is invalid.
    with open(BLOCKED_CHATS_FILE, 'w') as f:
        json.dump([], f)
    return []

def save_blocked_chat_ids(chat_ids):
    """Saves the list of blocked chat IDs to the JSON file."""
    with open(BLOCKED_CHATS_FILE, 'w') as f:
        json.dump(chat_ids, f)

# Define session file and load existing session if available.
SESSION_FILE = "session.json"
session_str = None
if os.path.exists(SESSION_FILE):
    with open(SESSION_FILE, "r") as f:
        session_str = f.read().strip()

# Initialize the Telegram client using a StringSession.
client = TelegramClient(StringSession(session_str), int(api_id), api_hash)

async def otp_login():
    """
    Performs login via OTP (phone number and code).
    """
    print("Starting OTP login...")
    await client.start()  # Prompts for phone number and OTP code.
    new_session = client.session.save()
    with open(SESSION_FILE, "w") as f:
        f.write(new_session)
    print("Logged in with OTP. Session saved to", SESSION_FILE)

async def qr_login():
    """
    Performs QR code login. Displays an ASCII QR code in the terminal.
    If two-step verification is enabled, it will prompt for a password.
    """
    if await client.is_user_authorized():
        print("Session is already authorized. No need for QR login.")
        return

    while True:
        print("Starting QR code login session...")
        try:
            qr = await client.qr_login()
        except Exception as e:
            print("Failed to initiate QR login:", e)
            return

        # Generate and display the ASCII QR code.
        qr_url = qr.url
        qr_obj = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr_obj.add_data(qr_url)
        qr_obj.make(fit=True)
        matrix = qr_obj.get_matrix()

        qr_ascii = ""
        for row in matrix:
            line = "".join("██" if col else "  " for col in row)
            qr_ascii += line + "\n"
        print(qr_ascii)
        print("Please scan the above QR code with your Telegram app.")

        try:
            await qr.wait()
            print("QR code login successful!")
            new_session = client.session.save()
            with open(SESSION_FILE, "w") as f:
                f.write(new_session)
            print("Session saved to", SESSION_FILE)
            break
        except Exception as e:
            error_message = str(e)
            print("QR code login attempt failed with error:", error_message)
            if "Two-steps verification" in error_message and "password is required" in error_message:
                print("Your account has two-step verification enabled. Please provide your password:")
                pw = input("Password: ")
                try:
                    await client.sign_in(password=pw)
                    print("Logged in successfully with two-step verification!")
                    new_session = client.session.save()
                    with open(SESSION_FILE, "w") as f:
                        f.write(new_session)
                    print("Session saved to", SESSION_FILE)
                    break
                except Exception as sign_e:
                    print("Failed to sign in with password:", sign_e)
                    break
            else:
                break

async def wait_for_owner_json():
    """
    Waits until the owner sends a JSON file (with a .json extension or JSON MIME type)
    and saves it as the BLOCKED_CHATS_FILE.
    """
    print("Waiting for owner to send the blocked chats JSON file...")
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    @client.on(events.NewMessage(from_users=owner_chat_id))
    async def json_handler(event):
        if event.message and event.message.document:
            doc = event.message.document
            file_name = getattr(doc, 'file_name', '')
            # Check if the file name ends with '.json' or if the MIME type is application/json.
            if (file_name and file_name.lower().endswith('.json')) or (doc.mime_type == "application/json"):
                if not future.done():
                    future.set_result(event)
                client.remove_event_handler(json_handler)

    event = await future
    downloaded = await event.message.download_media(file=BLOCKED_CHATS_FILE)
    print(f"Blocked chats JSON file received and saved as '{downloaded}'.")

# Media handler: Checks if the chat or sender is in the blocked list before downloading.
@client.on(events.NewMessage)
async def new_media_handler(event):
    if event.message and event.message.media:
        blocked = load_blocked_chat_ids()
        sender_id = event.message.sender_id
        # Check if the group/chat or the sender is blocked.
        if (event.chat_id in blocked) or (sender_id and sender_id in blocked):
            print(f"Skipping media download: Message from blocked chat ({event.chat_id}) or blocked sender ({sender_id}).")
            return

        # Use the sender's ID if available; otherwise, fallback to the chat ID.
        identifier = sender_id if sender_id else event.chat_id
        message_id = event.message.id

        # Determine media type (GIF or video) if possible.
        media_type = None
        if event.message.document:
            for attr in event.message.document.attributes:
                if isinstance(attr, DocumentAttributeAnimation):
                    media_type = "gif"
                    break  # Prioritize GIF detection.
                elif isinstance(attr, DocumentAttributeVideo):
                    media_type = "video"
        base_name = f"{identifier}-{message_id}"
        if media_type == "gif":
            file_name = base_name + "-gif"
        elif media_type == "video":
            file_name = base_name + "-video"
        else:
            file_name = base_name

        os.makedirs("downloads", exist_ok=True)
        file_path = os.path.join("downloads", file_name)
        saved_file = await event.message.download_media(file=file_path)
        print(f"New media downloaded to: {saved_file}")

# Command handler: /ban – adds a chat ID to the blocked list and sends updated JSON to owner.
@client.on(events.NewMessage(pattern='/ban'))
async def ban_handler(event):
    if event.sender_id != owner_chat_id:
        return  # Only process commands from the owner.
    parts = event.message.text.split()
    if len(parts) != 2:
        await event.reply("Usage: /ban <chat_id>")
        return
    try:
        target_chat_id = int(parts[1])
    except ValueError:
        await event.reply("Invalid chat ID.")
        return
    blocked = load_blocked_chat_ids()
    if target_chat_id in blocked:
        await event.reply(f"Chat {target_chat_id} is already banned.")
        return
    blocked.append(target_chat_id)
    save_blocked_chat_ids(blocked)
    await event.reply(f"Chat {target_chat_id} has been banned.")
    # Send the updated JSON file back to the owner.
    await client.send_file(owner_chat_id, BLOCKED_CHATS_FILE, caption="Updated blocked chats JSON file")

# Command handler: /unban – removes a chat ID from the blocked list and sends updated JSON to owner.
@client.on(events.NewMessage(pattern='/unban'))
async def unban_handler(event):
    if event.sender_id != owner_chat_id:
        return
    parts = event.message.text.split()
    if len(parts) != 2:
        await event.reply("Usage: /unban <chat_id>")
        return
    try:
        target_chat_id = int(parts[1])
    except ValueError:
        await event.reply("Invalid chat ID.")
        return
    blocked = load_blocked_chat_ids()
    if target_chat_id not in blocked:
        await event.reply(f"Chat {target_chat_id} is not banned.")
        return
    blocked.remove(target_chat_id)
    save_blocked_chat_ids(blocked)
    await event.reply(f"ID {target_chat_id} has been unbanned.")
    await client.send_file(owner_chat_id, BLOCKED_CHATS_FILE, caption="Updated blocked chats JSON file")

# Command handler: /info – retrieves information about the target chat or user.
@client.on(events.NewMessage(pattern='/info'))
async def info_handler(event):
    if event.sender_id != owner_chat_id:
        return  # Only process the command from the owner.
    
    parts = event.message.text.split()
    if len(parts) != 2:
        await event.reply("Usage: /info <chat_id>")
        return
    
    try:
        target_chat_id = int(parts[1])
    except ValueError:
        await event.reply("Invalid chat ID.")
        return
    
    try:
        # Attempt to fetch the entity.
        entity = await client.get_entity(target_chat_id)
    except Exception as e:
        # Handle failure—explain that the bot may not have interacted with this entity.
        await event.reply(f"Error retrieving chat info: {e}\n"
                          "Make sure the bot has interacted with that user or chat, or that the chat ID is valid.")
        return

    # Determine a friendly name for the entity.
    if hasattr(entity, 'title') and entity.title:
        chat_name = entity.title
    elif hasattr(entity, 'first_name'):
        chat_name = entity.first_name
        if hasattr(entity, 'last_name') and entity.last_name:
            chat_name += " " + entity.last_name
    else:
        chat_name = str(entity)
    
    await event.reply(f"Chat {target_chat_id} name: {chat_name}")

async def main():
    print("Choose login method:")
    print("1. OTP (via phone number and code)")
    print("2. QR code login")
    
    method = input("Enter your choice (1 or 2): ").strip()

    # Connect the client.
    await client.connect()
    
    if method == "1":
        await otp_login()
    elif method == "2":
        await qr_login()
    else:
        print("Invalid choice. Exiting.")
        await client.disconnect()
        sys.exit(1)
    
    os.makedirs("downloads", exist_ok=True)
    print("Login successful!")

    # At startup, wait for the owner to send the blocked chats JSON file.
    await wait_for_owner_json()
    print("Starting media downloader and command listener...")
    
    # Keep the client running to listen for incoming events.
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
