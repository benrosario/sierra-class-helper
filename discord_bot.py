#!/usr/bin/env python3
"""
Backward compatibility wrapper for Discord bot.
Runs the bot from src.bot.bot
"""
if __name__ == "__main__":
    from src.bot.bot import main
    import asyncio
    asyncio.run(main())
