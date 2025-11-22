"""
Discord bot for Sierra Class Helper
Connects to the FastAPI backend to provide course search via Discord
"""
import discord
from discord import app_commands
from discord.ext import commands
import os
import logging
import aiohttp
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
API_URL = os.environ.get("API_URL", "http://localhost:8000")

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN environment variable is required")

# Bot setup with intents
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned,  # Only respond to mentions, no prefix
    intents=intents,
    description="Sierra Class Helper - Your AI Academic Advisor"
)

class SierraClassHelper(commands.Cog):
    """Main cog for Sierra Class Helper functionality"""

    def __init__(self, bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        # Store conversation history per user: {user_id: [{role, content}, ...]}
        self.conversation_history = {}
        # Maximum messages to keep per user
        self.max_history_per_user = 20

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
        """Get conversation history for a user"""
        if user_id not in self.conversation_history:
            self.conversation_history[user_id] = []
        return self.conversation_history[user_id]

    def add_to_history(self, user_id: int, role: str, content: str):
        """Add a message to user's conversation history"""
        history = self.get_user_history(user_id)
        history.append({"role": role, "content": content})

        # Trim history if it gets too long
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
            # Get user's conversation history
            history = self.get_user_history(user_id)

            # Add user message to history
            self.add_to_history(user_id, "user", message)

            # Prepare API request with history
            request_data = {
                "message": message,
                "num_courses": num_courses,
                "conversation_history": history[:-1] if len(history) > 1 else None  # Exclude current message
            }

            async with self.session.post(
                f"{API_URL}/chat",
                json=request_data
            ) as response:
                if response.status == 200:
                    result = await response.json()

                    # Add assistant response to history
                    self.add_to_history(user_id, "assistant", result["response"])

                    return result
                else:
                    error_text = await response.text()
                    logger.error(f"API error {response.status}: {error_text}")
                    raise Exception(f"API returned status {response.status}")
        except Exception as e:
            logger.error(f"Failed to call chat API: {e}")
            raise

    @app_commands.command(name="ask", description="Ask Sierra Class Helper a question about courses")
    @app_commands.describe(question="Your question about Sierra College courses")
    async def ask_command(self, interaction: discord.Interaction, question: str):
        """Ask Sierra Class Helper a question about courses"""
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            logger.info(f"Question from {interaction.user}: {question}")

            # Call the API with user ID for conversation tracking
            result = await self.call_chat_api(question, interaction.user.id)

            # Discord has a 2000 character limit, so split if needed
            response_text = result["response"]

            if len(response_text) <= 2000:
                await interaction.followup.send(response_text, ephemeral=True)
            else:
                # Split into chunks
                chunks = [response_text[i:i+2000] for i in range(0, len(response_text), 2000)]
                await interaction.followup.send(chunks[0], ephemeral=True)
                for chunk in chunks[1:]:
                    await interaction.followup.send(chunk, ephemeral=True)

            logger.info(f"Response sent to {interaction.user}")

        except Exception as e:
            logger.error(f"Error processing question: {e}")
            await interaction.followup.send(
                "Sorry, I encountered an error processing your question. "
                "Please try again later or contact support.",
                ephemeral=True
            )

    @app_commands.command(name="clear", description="Clear your conversation history with the bot")
    async def clear_command(self, interaction: discord.Interaction):
        """Clear your conversation history with the bot"""
        self.clear_user_history(interaction.user.id)
        await interaction.response.send_message(
            "✅ Your conversation history has been cleared! I'll start fresh with your next message.",
            ephemeral=True
        )

    @app_commands.command(name="search", description="Search for courses (raw data without AI formatting)")
    @app_commands.describe(query="Search query for courses")
    async def search_command(self, interaction: discord.Interaction, query: str):
        """Search for courses (returns raw data without AI formatting)"""
        await interaction.response.defer(thinking=True, ephemeral=True)

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
                        await interaction.followup.send("No courses found matching your search.", ephemeral=True)
                        return

                    # Format courses nicely
                    output = f"**Found {len(courses)} courses:**\n\n"
                    for course in courses:
                        crn = course.get("CRN", "N/A")
                        subject = course.get("subject", "")
                        number = course.get("courseNumber", "")
                        title = course.get("courseTitle", "No title")

                        faculty = course.get("faculty", [])
                        instructor = faculty[0]["name"] if faculty else "No instructor"

                        output += f"**{subject}{number}** - {title}\n"
                        output += f"CRN: {crn} | Instructor: {instructor}\n\n"

                    if len(output) <= 2000:
                        await interaction.followup.send(output, ephemeral=True)
                    else:
                        await interaction.followup.send(output[:2000], ephemeral=True)
                else:
                    await interaction.followup.send("Failed to search courses. Please try again.", ephemeral=True)

        except Exception as e:
            logger.error(f"Error processing search: {e}")
            await interaction.followup.send(
                "Sorry, I encountered an error processing your search. "
                "Please try again later.",
                ephemeral=True
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
    """Handle incoming messages"""
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return

    # Check if bot is mentioned
    if bot.user in message.mentions:
        # Extract the message content without the mention
        content = message.content
        for mention in message.mentions:
            content = content.replace(f'<@{mention.id}>', '').replace(f'<@!{mention.id}>', '')
        content = content.strip()

        # If there's content after the mention, treat it as a question
        if content:
            async with message.channel.typing():
                try:
                    logger.info(f"Mention from {message.author}: {content}")

                    # Get the SierraClassHelper cog
                    cog = bot.get_cog("SierraClassHelper")
                    if cog:
                        # Call the chat API with user ID
                        result = await cog.call_chat_api(content, message.author.id)

                        # Discord has a 2000 character limit
                        response_text = result["response"]

                        if len(response_text) <= 2000:
                            await message.reply(response_text)
                        else:
                            # Split into chunks
                            chunks = [response_text[i:i+2000] for i in range(0, len(response_text), 2000)]
                            for chunk in chunks:
                                await message.channel.send(chunk)

                        logger.info(f"Response sent to {message.author}")
                    else:
                        await message.reply("Sorry, I'm having trouble processing your request right now.")

                except Exception as e:
                    logger.error(f"Error processing mention: {e}")
                    await message.reply(
                        "Sorry, I encountered an error processing your question. "
                        "Please try again later or use the `/ask` command."
                    )
        else:
            # Just mentioned without a question
            await message.reply(
                "Hi! I'm Sierra Class Helper. Ask me about courses!\n\n"
                "You can:\n"
                "- Mention me with a question: `@SierraClassHelper What CS classes are available?`\n"
                "- Use slash commands: `/ask <question>`, `/search <query>`, or `/clear`"
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
