from bc4py.config import C
from asyncio.streams import StreamReader, StreamWriter
from logging import getLogger
from typing import Optional, List, Deque
from collections import deque
import asyncio
import json

loop = asyncio.get_event_loop()
client_list: List['Client'] = list()  # working clients
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
        self.time_works = deque(maxlen=50)
        self.submit_span = submit_span
        self.n_accept = 0
        self.n_reject = 0

    def __repr__(self):
        return f"<Client {self.consensus_name} ver='{self.version}' " \
            f"'auth={self.username}:{self.password}' hashrate={self.hashrate_str}>"

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

    def average_submit_span(self):
        """weighted average submit span"""
        assert 1 < len(self.time_works)
        real = 0.0
        divide = 0
        old_ntime = self.time_works[0][0]
        for index, (ntime, _) in enumerate(self.time_works):
            real += (ntime - old_ntime) * index
            divide += index
            old_ntime = ntime
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
        sum_diff = 0.0
        for _, diff in self.time_works:
            sum_diff += diff
        # difficulty_in_600s = difficulty_in_Ns / 600 * N
        miner_diff = sum_diff * 600.0 / max(1, self.time_works[-1][0] - self.time_works[0][0])
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

    def close(self):
        self.f_enable = False
        self.writer.close()


async def response_success(client: Client, result, uuid):
    response = json.dumps({'result': result, 'error': None, 'id': uuid})
    client.writer.write(response.encode() + b'\n')
    await client.writer.drain()


async def response_failed(client: Client, error, uuid):
    response = json.dumps({'result': None, 'error': error, 'id': uuid})
    client.writer.write(response.encode() + b'\n')
    await client.writer.drain()


async def broadcast_clients(method, params, algorithm):
    data = json.dumps({'method': method, 'params': params, 'id': None})
    data = data.encode() + b'\n'
    count = 0
    for client in client_list:
        if client.algorithm == algorithm:
            try:
                client.writer.write(data)
                loop.call_soon(client.writer.drain)
                count += 1
            except ConnectionError:
                continue
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
