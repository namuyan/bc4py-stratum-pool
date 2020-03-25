from bc4py_stratum_pool.client import client_list


async def system_safe_exit() -> None:
    for client in client_list.copy():
        await client.close()
    raise NotImplementedError


__all__ = [
    "system_safe_exit",
]
