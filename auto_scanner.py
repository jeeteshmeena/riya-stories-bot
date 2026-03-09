import asyncio
import logging

from scanner_client import scan_channel
from config import CHANNEL_ID

logger = logging.getLogger(__name__)


async def auto_scan_loop():

    while True:

        try:

            logger.info("Auto scanning channel...")

            result = await scan_channel(CHANNEL_ID)

            logger.info(
                f"Auto Scan Complete | Stories: {result['stories']}"
            )

        except Exception as e:

            logger.error(f"Auto scan error: {e}")

        # scan every 10 minutes
        await asyncio.sleep(600)
