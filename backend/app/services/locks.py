"""生成与删除共用同一组会话锁，防止 checkpoint 和历史记录交错写入。"""

import asyncio
from weakref import WeakValueDictionary

conversation_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
