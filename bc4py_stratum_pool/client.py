from bc4py_stratum_pool.config import co_efficiency
from bc4py.config import C
from asyncio.streams import StreamReader, StreamWriter
from logging import getLogger
from typing import Optional, List, Deque
from collections import deque
from time import time
import asyncio
import json

loop = asyncio.get_event_loop()
client_list: List['Client'] = list()  # working clients
client_lock = asyncio.Lock()
closed_deque: Deque['Client'] = deque(maxlen=25)  # disconnected clients
log = getLogger(__name__)


class Client(object):
    __slots__ = ("f_enable", "reader", "writer", "algorithm",
                 "diff_list", "username", "password","account_id",
                 "subscription_id", "extranonce_1", "version",
                 "time_works", "submit_span", "n_accept", "n_reject")

    def __init__(self, reader, writer, algorithm, difficulty, submit_span):
        self.f_enable = True
        self.reader: StreamReader = reader
        self.writer: StreamWriter = writer
        self.algorithm: int = algorithm
        self.diff_list = deque(maxlen=5)
        self.diff_list.append(difficulty)
        self.username: Optional[str] = None
        self.password: Optional[str] = None
        self.account_id: Optional[int] = None
        self.subscription_id: Optional[bytes] = None
        self.extranonce_1: Optional[bytes] = None
        self.version: Optional[str] = None
        self.time_works = deque(maxlen=40)
        self.submit_span = submit_span
        self.n_accept = 0
        self.n_reject = 0

    def __repr__(self):
        return f"<Client {self.consensus_name} ver='{self.version}' " \
            f"'auth={self.username}:{self.password}' hashrate={self.hashrate_str}>"

    def get_peer_name(self) -> Optional[str]:
        host = self.writer.transport.get_extra_info('peername')
        if host is None:
            return None
        return host[0]

    @property
    def consensus_name(self):
        return C.consensus2name[self.algorithm]

    @property
    def difficulty(self):
        """get newest difficulty"""
        return self.diff_list[-1]

    @difficulty.setter
    def difficulty(self, value):
        """add new difficulty"""
        self.diff_list.append(value)

    def average_submit_span(self) -> Optional[float]:
        """weighted average submit span"""
        if len(self.time_works) < 2:
            return None
        real = 0.0
        divide = 0
        old_ntime = None
        time_limit = time() - 60 * 15  # limit latest 15min data
        for index, (ntime, _) in enumerate(filter(lambda x: time_limit < x[0], self.time_works)):
            if old_ntime is None:
                old_ntime = ntime
            real += (ntime - old_ntime) * index
            divide += index
            old_ntime = ntime
        if old_ntime is None:
            return None
        if divide == 0:
            return None
        return real / divide

    @property
    def hashrate(self) -> int:
        """
        7158278.8 = max_target / base_target
        network_hashrate(h/s) = difficulty * 7158278.8
        """
        # https://slushpool.com/help/terminology/
        if len(self.time_works) < 20:
            return 0
        count = 0
        sum_diff = 0.0
        begin_time = None
        time_limit = time() - 60 * 15  # limit latest 15min data
        for ntime, diff in filter(lambda x: time_limit < x[0], self.time_works):
            if begin_time is None:
                begin_time = ntime
            sum_diff += diff
            count += 1
        if count < 3:
            return 0
        if begin_time is None:
            return 0
        end_time = self.time_works[-1][0]
        # difficulty_in_600s = difficulty_in_Ns * 600 / N
        miner_diff = sum_diff * 600.0 / co_efficiency[self.algorithm] / max(1, end_time - begin_time)
        return int(miner_diff * 7158278.8)

    @property
    def hashrate_str(self):
        hashrate = self.hashrate
        if hashrate == 0:
            return "Calculating"
        elif hashrate < 1000:
            return f"{hashrate} Hash/s"
        elif hashrate < 1000000:
            return f"{round(hashrate/1000, 1)}K Hash/s"
        elif hashrate < 1000000000:
            return f"{round(hashrate/1000000, 1)}M Hash/s"
        else:
            return f"{round(hashrate/1000000000, 1)}G Hash/s"

    async def send(self, method, params, uuid):
        data = json.dumps({'method': method, 'params': params, 'id': uuid})
        self.writer.write(data.encode() + b'\n')
        await self.writer.drain()

    async def close(self):
        self.f_enable = False
        if not self.writer.transport.is_closing():
            self.writer.close()
        async with client_lock:
            if self in client_list:
                client_list.remove(self)


async def create_client(*args):
    client = Client(*args)
    async with client_lock:
        client_list.append(client)
    return client


async def response_success(client: Client, result, uuid):
    response = json.dumps({'result': result, 'error': None, 'id': uuid})
    client.writer.write(response.encode() + b'\n')
    await client.writer.drain()


async def response_failed(client: Client, error, uuid):
    response = json.dumps({'result': None, 'error': error, 'id': uuid})
    client.writer.write(response.encode() + b'\n')
    await client.writer.drain()


async def broadcast_clients(method, params, algorithm) -> int:
    """broadcast to ALL miners with a specific algorithm"""
    data = json.dumps({
        'method': method,
        'params': params,
        'id': None,
    })
    data = data.encode() + b'\n'
    count = 0
    async with client_lock:
        for client in client_list:
            try:
                if client.algorithm == algorithm:
                    client.writer.write(data)
                    await client.writer.drain()
                    count += 1
            except ConnectionResetError:
                # warning: don't remove from client_list
                log.debug(f"broadcast: connection reset by {client}")
            except Exception:
                log.error("broadcast_clients exception", exc_info=True)
    return count


# error response
OTHER_UNKNOWN = (20, "Other/Unknown")
JOB_NOT_FOUND = (21, "Job not found")
DUPLICATE_SHARE = (22, "Duplicate share")
LOW_DIFFICULTY_SHARE = (23, "Low difficulty share")
UNAUTHORIZED_WORKER = (24, "Unauthorized worker")
NOT_SUBSCRIBED = (25, "Not subscribed")


__all__ = [
    "client_list",
    "closed_deque",
    "Client",
    "create_client",
    "response_success",
    "response_failed",
    "broadcast_clients",
    "OTHER_UNKNOWN",
    "JOB_NOT_FOUND",
    "DUPLICATE_SHARE",
    "LOW_DIFFICULTY_SHARE",
    "UNAUTHORIZED_WORKER",
    "NOT_SUBSCRIBED",
]
