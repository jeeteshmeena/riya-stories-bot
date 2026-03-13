import asyncio
import logging
import time
from datetime import datetime
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    ChannelPrivateError,
    ChannelBannedError,
    ChatAdminRequiredError,
    MessageDeleteForbiddenError,
    MessageIdInvalidError,
    FloodWaitError
)
from config import API_ID, API_HASH, SESSION_STRING, LOG_CHANNEL
from database import load_db, save_link_flags, load_link_flags
from scanner_client import scan_channel

logger = logging.getLogger(__name__)

class BackgroundLinkChecker:
    def __init__(self):
        self.running = False
        self.client = None
        self.check_interval = 3600  # Check every hour
        
    async def start(self):
        """Start the background link checker"""
        if self.running:
            return
            
        self.running = True
        logger.info("Starting background link checker")
        
        # Initialize Telethon client
        self.client = TelegramClient(
            StringSession(SESSION_STRING),
            API_ID,
            API_HASH,
            timeout=30,
            connection_retries=3,
            retry_delay=5
        )
        
        await self.client.start()
        
        # Run the checker loop
        asyncio.create_task(self._checker_loop())
        
    async def stop(self):
        """Stop the background link checker"""
        self.running = False
        if self.client:
            await self.client.disconnect()
        logger.info("Background link checker stopped")
        
    async def _checker_loop(self):
        """Main checker loop"""
        while self.running:
            try:
                await self._check_all_links()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Link checker error: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes on error
                
    async def _check_all_links(self):
        """Check all story links for validity"""
        logger.info("Starting link validity check")
        
        db = load_db()
        link_flags = load_link_flags()
        changes_made = False
        
        for story_key, story in db.items():
            if not self.running:
                break
                
            link = story.get("link")
            if not link:
                continue
                
            try:
                # Extract message ID from link
                message_id = self._extract_message_id(link)
                if not message_id:
                    continue
                    
                # Try to access the message
                await self._check_link_validity(link, message_id, story_key, story, link_flags)
                changes_made = True
                
                # Rate limiting to avoid flood
                await asyncio.sleep(1)
                
            except FloodWaitError as e:
                logger.warning(f"Flood wait during link check: {e.seconds}s")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error(f"Error checking link {link}: {e}")
                
        if changes_made:
            save_link_flags(link_flags)
            logger.info("Link check completed, changes saved")
            
    def _extract_message_id(self, link):
        """Extract message ID from Telegram link"""
        try:
            # Extract from t.me/c/channel_id/message_id or t.me/username/message_id
            if "/c/" in link:
                parts = link.split("/c/")
                if len(parts) > 1:
                    message_part = parts[1].split("/")
                    if len(message_part) > 1:
                        return int(message_part[1])
            else:
                # t.me/username/message_id format
                parts = link.split("/")
                if len(parts) > 1:
                    try:
                        return int(parts[-1])
                    except ValueError:
                        pass
        except Exception:
            pass
        return None
        
    async def _check_link_validity(self, link, message_id, story_key, story, link_flags):
        """Check if a specific link is valid"""
        try:
            # Try to get the channel entity from the link
            entity = None
            if "/c/" in link:
                parts = link.split("/c/")
                if len(parts) > 1:
                    channel_id = parts[1].split("/")[0]
                    entity = int("-100" + channel_id)
            else:
                parts = link.split("/")
                if len(parts) > 1 and not parts[-2] == "t.me":
                    entity = parts[-2]
            
            if not entity:
                # If we couldn't parse the entity, we can't check
                return
                
            # Try to get the message
            message = await self.client.get_messages(entity, ids=message_id)
            
            if message is None:
                # Message deleted or inaccessible
                await self._mark_link_broken(story_key, story, link_flags, "Message deleted/not found")
            else:
                # Message exists, check if previously marked as broken
                if story_key in link_flags and link_flags[story_key].get("broken"):
                    await self._mark_link_fixed(story_key, story, link_flags)
                    
        except (ChannelPrivateError, ChannelBannedError, ChatAdminRequiredError):
            # Channel is private or restricted
            await self._mark_link_broken(story_key, story, link_flags, "Channel private/restricted")
        except (MessageDeleteForbiddenError, MessageIdInvalidError):
            # Message deleted or invalid
            await self._mark_link_broken(story_key, story, link_flags, "Message deleted/invalid")
        except Exception as e:
            logger.error(f"Unexpected error checking link {link}: {e}")
            
    async def _mark_link_broken(self, story_key, story, link_flags, reason):
        """Mark a link as broken"""
        if story_key not in link_flags:
            link_flags[story_key] = {}
            
        if not link_flags[story_key].get("broken"):
            link_flags[story_key].update({
                "broken": True,
                "link": story.get("link"),
                "reason": reason,
                "detected_at": datetime.now().isoformat(),
                "voters": link_flags[story_key].get("voters", []),
                "chats": link_flags[story_key].get("chats", [])
            })
            
            logger.warning(f"Link marked as broken: {story.get('text')} - {reason}")
            
            # Notify admin channel
            if LOG_CHANNEL:
                try:
                    await self.client.send_message(
                        LOG_CHANNEL,
                        f"⚠ **Automated Link Detection**\n\n"
                        f"📖 Story: {story.get('text', 'N/A')}\n"
                        f"🔗 Link: {story.get('link')}\n"
                        f"❌ Reason: {reason}\n"
                        f"⏰ Detected: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to send automated detection notification: {e}")
                    
    async def _mark_link_fixed(self, story_key, story, link_flags):
        """Mark a previously broken link as fixed"""
        if story_key in link_flags and link_flags[story_key].get("broken"):
            # Notify users who reported/voted for this link
            voters = link_flags[story_key].get("voters", [])
            chats = link_flags[story_key].get("chats", [])
            
            notification_text = (
                f"✅ **Link Fixed**\n\n"
                f"📖 Story: {story.get('text', 'N/A')}\n"
                f"🔗 Link: {story.get('link')}\n"
                f"⏰ Fixed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"The link is now working again!"
            )
            
            # Send notifications to chats where the vote occurred
            for chat_id in chats:
                try:
                    await self.client.send_message(chat_id, notification_text, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Failed to send fix notification to chat {chat_id}: {e}")
                    
            # Remove the broken flag
            del link_flags[story_key]
            logger.info(f"Link marked as fixed: {story.get('text')}")
            
            # Notify admin channel
            if LOG_CHANNEL:
                try:
                    voter_mentions = ", ".join([f"@{v.get('name', str(v.get('id')))}" for v in voters[:5]])
                    if len(voters) > 5:
                        voter_mentions += f" and {len(voters) - 5} others"
                        
                    await self.client.send_message(
                        LOG_CHANNEL,
                        f"✅ **Link Automatically Fixed**\n\n"
                        f"📖 Story: {story.get('text', 'N/A')}\n"
                        f"🔗 Link: {story.get('link')}\n"
                        f"⏰ Fixed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"👥 Notified: {voter_mentions}",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to send fix notification to admin channel: {e}")

# Global instance
link_checker = BackgroundLinkChecker()

async def start_link_checker():
    """Start the background link checker"""
    await link_checker.start()

async def stop_link_checker():
    """Stop the background link checker"""
    await link_checker.stop()
