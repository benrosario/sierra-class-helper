"""
Discord bot for Sierra Class Helper
Connects to the FastAPI backend to provide course search via Discord
"""
import discord
from discord import app_commands
from discord.ext import commands
import time
import logging
import aiohttp
from typing import Optional

from src.config import Config
from src.utils.course_formatting import informalName, summarize_meetings
from src.utils.campus import get_campus

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
if not Config.DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN environment variable is required")
DISCORD_TOKEN = Config.DISCORD_BOT_TOKEN
API_URL = Config.require_api_url()

# Bot setup with intents
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned,  # Only respond to mentions, no prefix
    intents=intents,
    description="Sierra Class Helper - Easily search for classes using natural language!"
)

RATE_LIMIT_MESSAGE = (
    "You're sending messages a little too fast for me to keep up. "
    "Wait a moment and try again."
)

# Disclaimer to append to each message
DISCLAIMER = "AI can make mistakes. Verify important information on the [official Sierra College website](<https://sierracollege.edu>)."

# Discord rejects messages longer than 2000 characters.
DISCORD_LIMIT = 2000

# How long a user's conversation context survives between messages, and how many
# turns we keep. Both small on purpose — this is throwaway short-term memory.
HISTORY_TTL = 30 * 60  # seconds
MAX_HISTORY_PER_USER = 10


def format_outgoing(text: str) -> list[str]:
    """
    Attach the disclaimer and split into Discord-sized (<=2000 char) chunks.

    The disclaimer rides on the final chunk when it fits, otherwise it's sent as
    its own trailing message — so every reply ends with it no matter how long the
    body is.
    """
    footer = f"\n\n{DISCLAIMER}"
    if len(text) + len(footer) <= DISCORD_LIMIT:
        return [text + footer]

    chunks = [text[i:i + DISCORD_LIMIT] for i in range(0, len(text), DISCORD_LIMIT)]
    if len(chunks[-1]) + len(footer) <= DISCORD_LIMIT:
        chunks[-1] += footer
    else:
        chunks.append(DISCLAIMER)
    return chunks


def _format_instructor(faculty: list, rating: str | None) -> str:
    """First instructor's display name (+ RateMyProfessors rating if present), or 'TBA'."""
    if not faculty:
        return "TBA"
    name = faculty[0].get("name") or "TBA"
    display = informalName(name) if "," in name else name
    if len(faculty) > 1:
        display += f" (+{len(faculty) - 1} more)"
    if rating:
        display += f" — RateMyProfessors: {rating}"
    return display


def format_course_block(course: dict) -> str:
    """
    Render one course as a compact, scannable block for /search.

    Pulls everything from the course dict plus the API-provided 'instructorRating'
    — no LLM involved. Anything missing degrades to a 'TBA'/'?' rather than erroring.
    Layout (one line each): course + title + CRN, instructor + rating, dates +
    campus, meeting schedule, seats, waitlist.
    """
    subject = course.get("subject", "")
    number = course.get("courseNumber", "")
    title = course.get("courseTitle", "No title")
    crn = course.get("CRN", "N/A")

    instructor = _format_instructor(course.get("faculty") or [], course.get("instructorRating"))

    meetings = course.get("meetings") or []
    when = summarize_meetings(meetings)
    if meetings:
        start = meetings[0].get("startDate") or "TBA"
        end = meetings[0].get("endDate") or "TBA"
        campus = get_campus(meetings[0].get("building", ""))
    else:
        start, end, campus = "TBA", "TBA", "Unknown Campus"

    enr = course.get("enrollment") or {}
    seats = f"{enr.get('enrolled', '?')}/{enr.get('max', '?')}"
    waitlist = f"{enr.get('waitCount', '?')}/{enr.get('waitCapacity', '?')}"

    return (
        f"📚 **{subject}{number}** | {title} | CRN: {crn}\n"
        f"👤 {instructor}\n"
        f"📅 {start} → {end}\n"
        f"📍 {campus}\n"
        f"🕒 {when}\n"
        f"💺 Seats: {seats}\n"
        f"📋 Waitlist: {waitlist}\n"
    )


async def _resolve_reference(message) -> str | None:
    """
    Return the text of the message this one is replying to, or None.

    Discord usually hands us the referenced message already resolved; if not (it
    fell out of the cache), we fetch it. Any failure — deleted message, missing
    permission — just yields None so the caller proceeds without the context.
    """
    ref = message.reference
    if ref is None:
        return None
    resolved = ref.resolved
    if resolved is None and ref.message_id:
        try:
            resolved = await message.channel.fetch_message(ref.message_id)
        except Exception:
            return None
    content = getattr(resolved, "content", None)
    return content or None


class RateLimited(Exception):
    """Raised when the upstream API returns HTTP 429."""


class SierraClassHelper(commands.Cog):
    """Main cog for Sierra Class Helper functionality"""

    def __init__(self, bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        # Short-term context per user: {user_id: [{role, content, ts}, ...]}.
        # In-memory only — it resets on restart, which is fine for casual chat.
        self.conversation_history = {}
        self.max_history_per_user = MAX_HISTORY_PER_USER

    async def cog_load(self):
        """Initialize HTTP session when cog loads"""
        self.session = aiohttp.ClientSession()
        logger.info("HTTP session initialized")

    async def cog_unload(self):
        """Cleanup HTTP session when cog unloads"""
        if self.session:
            await self.session.close()
            logger.info("HTTP session closed")

    def get_user_history(self, user_id: int) -> list:
        """
        Return the user's still-fresh history, dropping anything older than the
        TTL. Pruning on read keeps the store self-cleaning with no timers.
        """
        history = self.conversation_history.get(user_id)
        if history:
            # Prune in place so the stored list object stays stable across calls.
            cutoff = time.time() - HISTORY_TTL
            history[:] = [entry for entry in history if entry["ts"] >= cutoff]
        if not history:
            # Everything expired (or there was nothing) — forget the user entirely.
            self.conversation_history.pop(user_id, None)
            return []
        return history

    def add_to_history(self, user_id: int, role: str, content: str):
        """Append a turn (timestamped), pruning expired ones and capping the size."""
        self.get_user_history(user_id)  # prune expired entries first
        history = self.conversation_history.setdefault(user_id, [])
        history.append({"role": role, "content": content, "ts": time.time()})

        if len(history) > self.max_history_per_user:
            self.conversation_history[user_id] = history[-self.max_history_per_user:]

    def clear_user_history(self, user_id: int):
        """Clear conversation history for a user"""
        if user_id in self.conversation_history:
            del self.conversation_history[user_id]
            logger.info(f"Cleared conversation history for user {user_id}")

    async def call_chat_api(self, message: str, user_id: int, num_courses: int = 3) -> dict:
        """Call the chat API endpoint with conversation history"""
        try:
            # Snapshot the prior turns (already TTL-pruned) before adding this
            # message, and strip the internal 'ts' the API doesn't expect.
            prior = [
                {"role": entry["role"], "content": entry["content"]}
                for entry in self.get_user_history(user_id)
            ]
            self.add_to_history(user_id, "user", message)

            request_data = {
                "message": message,
                "num_courses": num_courses,
                "conversation_history": prior or None,
            }

            async with self.session.post(
                f"{API_URL}/chat",
                json=request_data,
                headers={"X-Discord-User": str(user_id)}
            ) as response:
                if response.status == 200:
                    result = await response.json()

                    # Add assistant response to history
                    self.add_to_history(user_id, "assistant", result["response"])

                    return result
                elif response.status == 429:
                    # Roll back the user message we optimistically added — the call didn't go through
                    self.get_user_history(user_id).pop()
                    raise RateLimited()
                else:
                    error_text = await response.text()
                    logger.error(f"API error {response.status}: {error_text}")
                    raise Exception(f"API returned status {response.status}")
        except RateLimited:
            raise
        except Exception as e:
            logger.error(f"Failed to call chat API: {e}")
            raise

    async def _send_followup(self, interaction: discord.Interaction, text: str):
        """Send a (deferred) slash-command reply, with the disclaimer + chunking."""
        for chunk in format_outgoing(text):
            await interaction.followup.send(chunk, ephemeral=False)

    @app_commands.command(name="ask", description="Ask Sierra Class Helper a question about courses")
    @app_commands.describe(question="Your question about Sierra College courses")
    async def ask_command(self, interaction: discord.Interaction, question: str):
        """Ask Sierra Class Helper a question about courses"""
        await interaction.response.defer(thinking=True, ephemeral=False)

        try:
            logger.info(f"Question from {interaction.user}: {question}")

            # Call the API with user ID for conversation tracking
            result = await self.call_chat_api(question, interaction.user.id)
            await self._send_followup(interaction, result["response"])

            logger.info(f"Response sent to {interaction.user}")

        except RateLimited:
            await self._send_followup(interaction, RATE_LIMIT_MESSAGE)
        except Exception as e:
            logger.error(f"Error processing question: {e}")
            await self._send_followup(
                interaction,
                "Sorry, I encountered an error processing your question. "
                "Please try again later or contact support.",
            )

    @app_commands.command(name="clear", description="Clear your conversation history with the bot")
    async def clear_command(self, interaction: discord.Interaction):
        """Clear your conversation history with the bot"""
        self.clear_user_history(interaction.user.id)
        chunks = format_outgoing(
            "✅ Your conversation history has been cleared! I'll start fresh with your next message."
        )
        await interaction.response.send_message(chunks[0], ephemeral=False)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=False)

    @app_commands.command(name="search", description="Search for courses (raw data without AI formatting)")
    @app_commands.describe(query="Search query for courses")
    async def search_command(self, interaction: discord.Interaction, query: str):
        """Search for courses (returns raw data without AI formatting)"""
        await interaction.response.defer(thinking=True, ephemeral=False)

        try:
            logger.info(f"Search from {interaction.user}: {query}")

            async with self.session.post(
                f"{API_URL}/search",
                json={"query": query, "num_results": 3}
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    courses = result["courses"]

                    if not courses:
                        await self._send_followup(interaction, "❌ No courses found matching your search.")
                        return

                    output = f"**Found {len(courses)} course(s):**\n\n"
                    output += "\n".join(format_course_block(course) for course in courses)

                    # Remember what we showed so a follow-up ("what time does it
                    # meet?") has the courses in context. Store the clean text —
                    # the disclaimer is presentation-only and added at send time.
                    self.add_to_history(interaction.user.id, "user", f"Searched courses: {query}")
                    self.add_to_history(interaction.user.id, "assistant", output)

                    await self._send_followup(interaction, output)
                else:
                    await self._send_followup(interaction, "Failed to search courses. Please try again.")

        except Exception as e:
            logger.error(f"Error processing search: {e}")
            await self._send_followup(
                interaction,
                "Sorry, I encountered an error processing your search. "
                "Please try again later.",
            )

@bot.event
async def on_ready():
    """Called when the bot is ready"""
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} guilds")
    logger.info(f"API URL: {API_URL}")

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")

    # Set bot status
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="/ask and /search commands"
        )
    )

@bot.event
async def on_message(message):
    """Handle @mentions in the allowed channel.

    Ignores DMs entirely and, when SIERRA_BOT_CHANNEL_ID is set, ignores any
    channel other than that one. Slash commands are handled separately and
    aren't affected by this gate.
    """
    if message.author == bot.user:
        return

    if isinstance(message.channel, discord.DMChannel):
        return

    if bot.user not in message.mentions:
        return

    if Config.BOT_CHANNEL_IDS and message.channel.id not in Config.BOT_CHANNEL_IDS:
        return

    # Strip the @mention prefix so the model doesn't see "<@1234567> what classes..."
    content = message.content
    for mention in message.mentions:
        content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
    content = content.strip()

    async def respond(text: str) -> None:
        chunks = format_outgoing(text)
        await message.reply(chunks[0])
        for chunk in chunks[1:]:
            await message.channel.send(chunk)

    if not content:
        await respond(
            "Hi! I'm Sierra Class Helper. Ask me about courses!\n\n"
            "You can:\n"
            "- Mention me in this channel: '@SierraClassHelper What CS classes are available?'\n"
            "- Use slash commands: '/ask <question>', '/search <query>', or '/clear'"
        )
        return

    # If this message is a reply to something, pull that message in as context so
    # follow-ups like "what are the times?" know which courses we mean.
    query = content
    referenced = await _resolve_reference(message)
    if referenced:
        query = f'(Replying to an earlier message: "{referenced[:500]}")\n{content}'

    async with message.channel.typing():
        try:
            logger.info(f"mention from {message.author}: {content}")

            cog = bot.get_cog("SierraClassHelper")
            if not cog:
                await respond("Sorry, I'm having trouble processing your request right now.")
                return

            result = await cog.call_chat_api(query, message.author.id)
            await respond(result["response"])

            logger.info(f"Response sent to {message.author}")

        except RateLimited:
            await respond(RATE_LIMIT_MESSAGE)
        except Exception as e:
            logger.error(f"Error processing mention: {e}")
            await respond(
                "Sorry, I encountered an error processing your question. "
                "Please try again later or use the '/ask' command."
            )

async def main():
    """Main function to start the bot"""
    async with bot:
        # Add cog
        await bot.add_cog(SierraClassHelper(bot))

        # Start the bot
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
