import asyncio
import logging
import sys

from config import BotConfig
from discord_adapter import ClaudeCodeBot


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("discord").setLevel(logging.WARNING)

    config = BotConfig.from_env()

    if not config.discord_token:
        logging.error("DISCORD_TOKEN environment variable is required")
        sys.exit(1)

    bot = ClaudeCodeBot(config)
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logging.info("Shutting down...")


if __name__ == "__main__":
    main()
