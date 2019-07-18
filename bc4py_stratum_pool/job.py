from bc4py_stratum_pool.config import *
from bc4py_stratum_pool.ask import *
from bc4py.config import C
from bc4py.chain.tx import TX
from bc4py.chain.block import Block
from bc4py.chain.utils import bits2target
from bc4py.chain.workhash import update_work_hash
from bc4py_extension import merkleroot_hash, sha256d_hash, PyAddress
from expiringdict import ExpiringDict
from binascii import a2b_hex
from logging import getLogger
from typing import Optional, List, Tuple, Dict
from time import time
import asyncio

loop = asyncio.get_event_loop()
lock = asyncio.Lock()
# job will expired in 5min
job_dict: Dict[int, 'Job'] = ExpiringDict(max_len=2000, max_age_seconds=300)
DEFAULT_TARGET = float(0x00000000ffff0000000000000000000000000000000000000000000000000000)
log = getLogger(__name__)


class Job(object):
    __slots__ = ("job_id", "previous_hash", "coinbase1", "coinbase2",
                 "unconfirmed", "version", "bits", "ntime", "height",
                 "algorithm", "submit_hashs", "create_time")

    def __init__(self, job_id, previous_hash, coinbase, unconfirmed, version, bits, ntime, height, algorithm):
        """
        coinbase: [coinbase1]-[extranonce1 4bytes]-[extranonce2 4bytes]-[dummy 4bytes = coinbase2]
        unconfirmed: [(hash, data), ...]
        submit_works: [(blockhash),...]
        """
        self.job_id: int = job_id
        self.previous_hash: bytes = previous_hash
        self.coinbase1: bytes = coinbase[:-8]
        self.coinbase2 = b''  # coinbase[-0:]
        self.unconfirmed: List[Tuple[bytes, bytes]] = unconfirmed
        self.version = version
        self.bits: bytes = bits
        self.ntime: int = ntime
        self.height: int = height
        self.algorithm: int = algorithm
        self.submit_hashs = list()
        self.create_time = time()

    def __repr__(self):
        return f"<Job {hex(self.job_id)} {C.consensus2name[self.algorithm]} " \
            f"height={self.height} time={self.ntime} diff={self.difficulty} txs={len(self.unconfirmed)}>"

    @property
    def difficulty(self):
        return round(DEFAULT_TARGET / bits2target(int.from_bytes(self.bits, 'big')), 8)


def get_submit_data(job: Job, extranonce1: bytes, extranonce2: bytes, nonce: bytes, difficulty: float) \
        -> (Optional[bytes], bytes, bool, bool):
    """check client submit data and generate node submit data"""
    assert len(extranonce1) == 4 and len(extranonce2) == 4
    coinbase = job.coinbase1 + extranonce1 + extranonce2 + job.coinbase2
    coinbase_hash = sha256d_hash(coinbase)
    merkleroot_list = [coinbase_hash] + [tx[0] for tx in job.unconfirmed]
    merkleroot = merkleroot_hash(merkleroot_list)
    block = Block.from_dict({
        'version': job.version,
        'previous_hash': job.previous_hash,
        'merkleroot': merkleroot,
        'time': job.ntime,
        'bits': int.from_bytes(job.bits, 'big'),
        'nonce': nonce,
        # meta
        'height': job.height,
        'flag': job.algorithm,
    })
    # check fulfill target or share
    update_work_hash(block)
    f_mined = block.pow_check()
    f_shared = block.pow_check(int(DEFAULT_TARGET / difficulty))
    log.debug(f"block -> {block.height} {block.hash.hex()}")
    log.debug(f"coinbase -> {coinbase.hex()}")
    log.debug(f"header -> {block.b.hex()}")
    log.debug(f"merkleroot -> {len(merkleroot_list)} {merkleroot.hex()}")
    log.debug(f"workhash -> {block.work_hash.hex()} mined:{f_mined} shared:{f_shared}")
    # generate submit data when mined
    if f_mined:
        submit_data = block.b
        tx_len = len(job.unconfirmed) + 1
        if tx_len < 0xfd:
            submit_data += tx_len.to_bytes(1, 'little')
        elif tx_len <= 0xffff:
            submit_data += b'\xfd' + tx_len.to_bytes(2, 'little')
        elif tx_len <= 0xffffffff:
            submit_data += b'\xfe' + tx_len.to_bytes(4, 'little')
        elif tx_len <= 0xffffffffffffffff:  # == 0xff
            submit_data += b'\xff' + tx_len.to_bytes(8, 'little')
        else:
            raise Exception(f"overflowed tx length {tx_len}")
        submit_data += coinbase
        for tx in job.unconfirmed:
            submit_data += tx[1]
    else:
        submit_data = None
    return submit_data, block, f_mined, f_shared


async def add_new_job(algorithm: int, force_renew=False) -> Job:
    """
    :param algorithm: specify by algorithm int
    :param force_renew: flag use template method
    """
    async with lock:
        if len(job_dict) == 0:
            job_id = 1
        else:
            job_id = max(job_dict) + 1
        # generate new job
        latest_job = get_best_job(algorithm)
        if force_renew or latest_job is None:
            # get block template
            params = [{'capabilities': ['coinbasetxn', 'messagenonce']}]
            template = None
            while template is None:
                template = await ask_json_rpc('getblocktemplate', params, 'user', str(algorithm))
            # new job
            previous_hash = a2b_hex(template['previousblockhash'])[::-1]
            coinbase = a2b_hex(template['coinbasetxn']['data'])
            if Const.PAYOUT_METHOD == 'transaction':
                pass
            elif Const.PAYOUT_METHOD == 'coinbase':
                coinbase_tx = TX.from_binary(coinbase)
                for dist in reversed(distribution_list):
                    if dist.algorithm != algorithm:
                        continue
                    if len(dist.distribution) < 2:
                        continue
                    owner_address, _, reward = coinbase_tx.outputs[0]
                    coinbase_tx.outputs.clear()
                    reward -= (len(dist.distribution) - 1) * C.EXTRA_OUTPUT_REWARD_FEE
                    for address, ratio in dist.distribution:
                        if address is None:
                            coinbase_tx.outputs.append((owner_address, 0, int(reward * ratio)))
                        else:
                            coinbase_tx.outputs.append((PyAddress.from_string(address), 0, int(reward * ratio)))
                    coinbase_tx.serialize()
                    # over write new coinbase
                    coinbase = coinbase_tx.b
                    log.debug(f"overwrite new coinbase outputs={len(dist.distribution)}")
                    break
                else:
                    log.debug("no distribution data, no edit coinbase")
            else:
                log.warning(f"not found payout method '{Const.PAYOUT_METHOD}'")
            unconfirmed = [(a2b_hex(tx['hash'])[::-1], a2b_hex(tx['data'])) for tx in template['transactions']]
            version = template['version']
            bits = a2b_hex(template['bits'])
            ntime = template['time']
            height = template['height']
        else:
            # just update blocktime
            increase_time = int(time() - latest_job.create_time)
            previous_hash = latest_job.previous_hash
            coinbase_tx = TX.from_binary(latest_job.coinbase1 + b'\x00' * 8 + latest_job.coinbase2)
            coinbase_tx.time += increase_time
            coinbase_tx.deadline += increase_time
            coinbase_tx.serialize()
            coinbase = coinbase_tx.b
            unconfirmed = latest_job.unconfirmed
            version = latest_job.version
            bits = latest_job.bits
            ntime = latest_job.ntime + increase_time
            height = latest_job.height
        new_job = Job(job_id, previous_hash, coinbase, unconfirmed, version, bits, ntime, height, algorithm)
        job_dict[job_id] = new_job
    return new_job


def get_job_by_id(job_id: int) -> Optional[Job]:
    if job_id in job_dict:
        return job_dict[job_id]
    else:
        return None


def get_best_job(algorithm) -> Optional[Job]:
    try:
        for job in sorted(job_dict.values(), key=lambda x: x.create_time, reverse=True):
            if algorithm == job.algorithm:
                return job
    except Exception:
        log.error("get_best_job", exc_info=True)
    return None


__all__ = [
    "Job",
    "get_submit_data",
    "add_new_job",
    "get_job_by_id",
    "get_best_job",
]
