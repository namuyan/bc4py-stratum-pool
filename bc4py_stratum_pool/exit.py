from bc4py_stratum_pool.client import client_list
from bc4py_stratum_pool.stratum import stratum_list
import asyncio


async def system_safe_exit():
    for client in client_list.copy():
        await client.close()
    raise NotImplementedError


__all__ = [
    "system_safe_exit",
]
