from bc4py_stratum_pool.config import Const
from typing import List, Dict, Any
from logging import getLogger, WARNING
import aiohttp


log = getLogger(__name__)
getLogger('aiohttp').setLevel(WARNING)


async def ask_get(method: str, params: dict = None) -> dict:
    """ask node by GET method"""
    async with aiohttp.ClientSession() as session:
        async with session.get(Const.REST_API + method, params=params) as response:
            if response.status == 200:
                data = await response.json()
                log.debug(f"REST GET method={method} params={params} success")
                return data
            else:
                text = await response.text()
                log.error(f"REST GET method={method} params={params} error={text}")
                raise ConnectionError(text)


async def ask_post(method: str, json: Dict[str, Any]) -> dict:
    """ask node by POST method"""
    async with aiohttp.ClientSession() as session:
        async with session.post(Const.REST_API + method, json=json) as response:
            if response.status == 200:
                data = await response.json()
                log.debug(f"REST POST method={method} json={json} success={data}")
                return data
            else:
                text = await response.text()
                log.error(f"REST POST method={method} json={json} error={text}")
                raise ConnectionError(text)


async def ask_json_rpc(method: str, params: List[Any], user: str, pwd: str) -> dict:
    """ask node by json-rpc"""
    json = {
        'method': method,
        'params': params,
        'id': None,
    }
    async with aiohttp.ClientSession() as session:
        auth = aiohttp.BasicAuth(user, pwd)
        async with session.post(Const.REST_API, json=json, auth=auth) as response:
            if response.status == 200:
                data = await response.json()
                log.debug(f"JSON-RPC method={method} params={params} success={data}")
                return data['result']
            else:
                text = await response.text()
                log.error(f"JSON-RPC method={method} params={params} error={text}")
                raise ConnectionError(text)


__all__ = [
    "ask_get",
    "ask_post",
    "ask_json_rpc",
]
