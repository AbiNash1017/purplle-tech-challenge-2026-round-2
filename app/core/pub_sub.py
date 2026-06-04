"""
In-process Pub/Sub broker.
Replaces Redis Pub/Sub (not supported by Upstash REST) with asyncio Queues.
All publishers and subscribers in the same process share this broker.
"""
import asyncio
import logging
from collections import defaultdict
from typing import Dict, List

logger = logging.getLogger(__name__)


class PubSubBroker:
    def __init__(self):
        # channel -> list of asyncio.Queue subscribers
        self._channels: Dict[str, List[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, channel: str) -> asyncio.Queue:
        """Create and register a new queue for this channel. Returns the queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._channels[channel].append(q)
        logger.debug(f"PubSub: new subscriber on '{channel}' (total={len(self._channels[channel])})")
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue):
        """Remove a subscriber queue from the channel."""
        try:
            self._channels[channel].remove(q)
            logger.debug(f"PubSub: subscriber removed from '{channel}' (total={len(self._channels[channel])})")
        except ValueError:
            pass

    async def publish(self, channel: str, message: str):
        """Publish a message to all subscribers of a channel."""
        queues = self._channels.get(channel, [])
        dead = []
        for q in queues:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning(f"PubSub: queue full on '{channel}', dropping message for slow subscriber")
            except Exception as e:
                logger.error(f"PubSub: error publishing to '{channel}': {e}")
                dead.append(q)
        # Prune dead queues
        for q in dead:
            self.unsubscribe(channel, q)

    def subscriber_count(self, channel: str) -> int:
        return len(self._channels.get(channel, []))


# Singleton shared across the entire app
pubsub_broker = PubSubBroker()
