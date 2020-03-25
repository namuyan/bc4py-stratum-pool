from bc4py_stratum_pool.config import Const
from bc4py_stratum_pool import methods
from bc4py_stratum_pool.client import *
from bc4py_stratum_pool.commands import mining_set_difficulty, client_reconnect
from asyncio.streams import StreamReader, StreamWriter
from bc4py.config import C
from typing import TYPE_CHECKING, List, Tuple, Any, Callable, Coroutine, Generator
from logging import getLogger
from collections import namedtuple
import asyncio
import json

loop = asyncio.get_event_loop()
log = getLogger(__name__)
SOCKET_TIMEOUT = 1200  # 20min
Stratum = namedtuple('Stratum', ['port', 'algorithm', 'difficulty', 'variable_diff'])
stratum_list: List[Stratum] = list()

# typing
if TYPE_CHECKING:
    _STRATUM_SERVER = Coroutine[Any, Any, asyncio.AbstractServer]
else:
    _STRATUM_SERVER = Generator[Any, None, asyncio.AbstractServer]


async def get_atomic_message(prefix: bytes, reader: StreamReader) -> Tuple[Any, bytes]:
    """receive message one by one"""
    data = prefix
    while True:
        data += await asyncio.wait_for(reader.read(1024), SOCKET_TIMEOUT, loop=loop)
        if b'\n' not in data:
            continue
        raw, prefix = data.split(b'\n', 1)
        msg = json.loads(raw)
        return msg, prefix


def stratum_handle(
        algorithm: int,
        difficulty: float,
        variable_diff: bool = True,
        submit_span: float = 30.0
) -> Callable[[StreamReader, StreamWriter], Coroutine[Any, Any, None]]:
    """
    :param algorithm: mining algorithm number
    :param difficulty: start difficulty
    :param variable_diff: auto adjust difficulty flag
    :param submit_span: submit share span
    """
    async def handle(reader: StreamReader, writer: StreamWriter) -> None:
        # create new client
        client = await create_client(reader, writer, algorithm, difficulty, submit_span)
        log.info(f"new client join {client.get_peer_name()}")
        try:
            # note: some miners hate quick difficulty notification
            if variable_diff:
                asyncio.run_coroutine_threadsafe(schedule_dynamic_difficulty(client), loop)
            # notify first difficulty
            asyncio.run_coroutine_threadsafe(wrap_with_delay(5, mining_set_difficulty, client), loop)
            # wait for data
            prefix = b''
            while client.f_enable:
                msg, prefix = await get_atomic_message(prefix, reader)
                # check client status
                if 100 < client.n_reject and client.n_accept < client.n_reject:
                    port = writer.transport.get_extra_info('sockname')[1]
                    await client_reconnect(client, Const.HOST_NAME, port)
                    log.debug("too match fail, ask client reconnect")
                    break
                # receive correct message
                method = msg.get('method')
                if method is None:
                    raise ConnectionError('Not found method')
                if not isinstance(method, str):
                    raise ConnectionError('method is not string "{}"'.format(method))
                if not (method.startswith('mining.') or method.startswith('client.')):
                    raise ConnectionError('method format is not correct "{}"'.format(method))
                params = msg.get('params', list())
                # throw task
                function = getattr(methods, method.replace('.', '_'), None)
                if function is None:
                    await response_failed(client, OTHER_UNKNOWN, msg.get('id'))
                    continue  # ignore
                log.debug(f"stratum request id={msg.get('id')} method={method} params={params}")
                comment = await function(client, params, msg.get('id'))
                # response
                if comment is not None:
                    log.info(f"stratum get comment '{comment}'")
                continue
        except ConnectionError as e:
            log.debug("self disconnect")
        except asyncio.TimeoutError:
            log.info("socket response timeout")
        except Exception:
            log.error("unexpected exception", exc_info=True)
        # close
        if client.subscription_id:
            closed_deque.append(client)
        await client.close()
        log.info(f"close and remove {client}")
    # wrap handle
    return handle


def stratum_server(
        port: int,
        algorithm: int,
        difficulty: float,
        variable_diff: bool = True,
        host: str = '0.0.0.0'
) -> _STRATUM_SERVER:
    assert algorithm in C.consensus2name
    # port duplication check
    for stratum in stratum_list:
        assert port != stratum.port
    # algorithm id check
    algorithm_name = C.consensus2name[algorithm]
    log.info(f"add new stratum {algorithm_name} stratum+tcp://{host}:{port} "
             f"diff={difficulty} variable_diff={variable_diff}")
    stratum_list.append(Stratum(port, algorithm_name, difficulty, variable_diff))
    stratum = stratum_handle(algorithm, difficulty, variable_diff)
    return asyncio.start_server(stratum, host, port, loop=loop)


async def wrap_with_delay(sec, func, *args) -> None:  # type: ignore
    """add delay for async fnc"""
    await asyncio.sleep(sec)
    await func(*args)


async def schedule_dynamic_difficulty(client: Client, schedule_span: int = 90) -> None:
    """
    adjust difficulty at regular interval
    node: short schedule span often cause low-difficulty-share reject
    """
    last_update_bias = 0.0
    min_difficulty = round(client.difficulty / 1000, 8)
    while client.f_enable:
        try:
            await asyncio.sleep(schedule_span)
            if not client.f_enable:
                continue  # client closed
            if client.subscription_id is None:
                continue  # client not subscribed
            elif len(client.time_works) < 2:
                # beginning diff is too high
                new_difficulty = round(client.difficulty * 0.5, 8)
            elif len(client.time_works) < 10:
                continue  # wait for enough work stored
            else:
                # client has enough data to adjust
                real_span = client.average_submit_span()
                if real_span is None:
                    # bind to high difficulty
                    new_difficulty = round(client.difficulty * 0.7, 8)
                else:
                    bias = client.submit_span / max(1.0, real_span)
                    if bias == last_update_bias:
                        continue
                    last_update_bias = bias
                    if 0.90 < bias < 1.1:
                        continue
                    new_difficulty = round(client.difficulty * max(min(bias, 1.3), 0.7), 8)
            # adjust difficulty
            if new_difficulty < min_difficulty:
                log.debug(f"ignore too low difficulty {new_difficulty} < {min_difficulty}")
                continue
            log.debug(f"adjust difficulty {client.difficulty} -> {new_difficulty}")
            client.difficulty = new_difficulty
            await mining_set_difficulty(client)
        except ConnectionError as e:
            log.warning(f"connection error by {str(e)} on {client}")
            await client.close()
        except Exception:
            log.error("difficulty scheduler exception", exc_info=True)


__all__ = [
    "stratum_list",
    "stratum_server",
]
