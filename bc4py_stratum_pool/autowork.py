from bc4py_stratum_pool.config import *
from bc4py_stratum_pool.job import *
from bc4py_stratum_pool.commands import *
from bc4py_stratum_pool.account import *
from bc4py_stratum_pool.client import client_list
from bc4py_stratum_pool.ask import *
from bc4py.config import C
from collections import deque, namedtuple, defaultdict
from typing import Deque
from logging import getLogger
from asyncio import wait_for
from binascii import a2b_hex
from time import time
import aiohttp
import asyncio


log = getLogger(__name__)
loop = asyncio.get_event_loop()
block_notify_que = asyncio.queues.Queue()
block_history_list = deque(maxlen=50)
tx_history_list = deque(maxlen=50)
consensus_list = list()
f_enable = True


async def auto_distribution_recode(
        algorithm_list: list, owner_fee=0.05, job_span=60, search_span=10800):
    """recode miner's distribution"""
    assert 0.0 < owner_fee < 1.0
    max_outputs_num = 255
    log.info("auto distribution recode start")
    global f_enable
    while f_enable:
        try:
            await asyncio.sleep(job_span)
            async with create_db(Const.DATABASE_PATH, strict=True) as db:
                cur = await db.cursor()
                end = time()
                begin = end - search_span
                for algorithm in algorithm_list:
                    account_shares = await read_distribution_shares(
                        cur=cur, begin=begin, end=end, algorithm=algorithm)
                    if len(account_shares) == 0:
                        # no miner found, only owner output
                        distribution = [(None, 1.0)]
                        distribution_list.append(Distribution(
                            int(end), algorithm, distribution))
                        continue
                    # limit distribution num
                    over_size = len(account_shares) + 1 - max_outputs_num  # miners + owner - max_num
                    if 0 < over_size:
                        order = sorted(account_shares.items(), key=lambda x: x[1], reverse=True)
                        while 0 < over_size:
                            account_id, share = order.pop()
                            del account_shares[account_id]
                            over_size -= 1
                            log.debug(f"remove from share id={account_id} share={round(share, 8)}")
                    # calculate distribution
                    total_share = sum(account_shares.values()) / (1 - owner_fee)
                    distribution = [
                        (await read_account_id2address(cur=cur, account_id=account_id), share / total_share)
                        for account_id, share in account_shares.items()]
                    distribution.insert(0, (None, owner_fee))
                    # recode distribution
                    distribution_list.append(Distribution(
                        int(end), algorithm, distribution))
                    log.debug(f"recode distribution algorithm={algorithm} len={len(distribution)}")
        except Exception:
            log.debug("auto_distribution_recode exception", exc_info=True)


async def auto_payout_system(
        min_confirm: int, min_amount=5000000000, owner_fee=0.05, ignore_amount=10000, check_span=3600):
    """
    auto payout to miners
    :param min_confirm: minimum confirmation heights
    :param min_amount: minimum send amount at once
    :param owner_fee: Owner's share ratio (note: owner pay sending fee)
    :param ignore_amount: ignore too few sending
    :param check_span: loop check span
    """
    assert 0.0 < owner_fee < 1.0
    assert Const.PAYOUT_METHOD == 'transaction'
    global f_enable
    while f_enable:
        await asyncio.sleep(check_span)
        log.info("auto payout process start")
        async with create_db(Const.DATABASE_PATH, strict=True) as db:
            try:
                cur = await db.cursor()
                # get best height on chain
                best_info = await ask_get('/public/getchaininfo')
                best_height = best_info['best']['height']
                # find mined block status
                total_mined_amount = 0
                total_block_count = 0
                end = None
                async for ntime, blockhash in iter_latest_mined_shares(cur):
                    params = {'hash': blockhash.hex(), 'txinfo': 'true'}
                    try:
                        block = await ask_get('/public/getblockbyhash', params)
                    except ConnectionError as e:
                        log.warning(f"orphan? REST returns error {e} by {params}")
                        continue
                    if best_height - min_confirm < block['height']:
                        continue
                    # mined block
                    if end is None:
                        end = float(ntime)
                    if block['f_orphan']:
                        continue
                    address, coin_id, amount = block['txs'][0]['outputs'][0]
                    total_mined_amount += amount
                    total_block_count += 1
                # check
                total_send_amount = int(total_mined_amount * (1.0 - owner_fee))
                if min_amount > total_send_amount:
                    log.info(f"too few mined amount {min_amount} > {total_send_amount}")
                    continue
                if end is None:
                    log.info("end time is not defied")
                    continue
                # let's try to send
                log.debug(f"total send amount is {total_send_amount}, owner get {total_mined_amount-total_send_amount}")
                # find begin time
                begin = await read_last_unpaid_time(cur)
                # calculate distribution
                related_accounts = await read_related_accounts(cur=cur, begin=begin, end=end)
                account_share_dict = dict()
                for account_id in related_accounts:
                    account_share_dict[account_id] = await read_account_unpaid_shares(
                        cur=cur, begin=begin, end=end, account_id=account_id)
                log.debug(f"auto send span {begin} -> {end}")
                # setup payout pairs
                total_share = sum(account_share_dict.values())
                payout_pairs = list()
                paid_accounts = list()
                for account_id, share in account_share_dict.items():
                    address = await read_account_id2address(cur=cur, account_id=account_id)
                    amount = int(total_send_amount * share / total_share)
                    if ignore_amount < amount:
                        payout_pairs.append((address, 0, amount))
                        paid_accounts.append(account_id)
                    else:
                        log.debug(f"ignore by too low share id={account_id} amount={amount}")
                if len(payout_pairs) == 0:
                    log.info(f"no payout accounts")
                    continue
                # try to send
                params = {'pairs': payout_pairs}
                result = await ask_post('/private/sendmany', params)
                log.info(f"success payout! {result['hash']}")
                # recode payout
                payout_id = await insert_new_transaction(
                    cur=cur, txhash=a2b_hex(result['hash']), amount=total_send_amount, begin=begin, end=end)
                log.info(f"success recode payout id={payout_id}")
                await update_shares_as_paid(
                    cur=cur, payout_id=payout_id, begin=begin, end=end, accounts=paid_accounts)
                log.info(f"success update shares row={cur.rowcount}")
                await db.commit()
            except DatabaseError:
                log.debug("database error", exc_info=True)
                await db.rollback()
            except Exception:
                log.error("auto_payout_system exception", exc_info=True)
                await db.rollback()


async def auto_pool_status_recode(job_span=60):
    """recode pool status for dashboard.html"""
    global f_enable
    log.info("start auto recode status")
    last_update_time = int(time())
    while f_enable:
        try:
            await asyncio.sleep(job_span)
            async with create_db(Const.DATABASE_PATH) as db:
                cur = await db.cursor()
                ntime = int(time())
                # mined share
                share = await read_total_unpaid_shares(cur=cur, begin=last_update_time, end=ntime, f_raise=False)
                # workers
                workers = defaultdict(int)
                for client in client_list:
                    workers[client.consensus_name] += 1
                workers = tuple(workers.items())
                # pool hashrate
                pool_hashrate = defaultdict(int)
                for client in client_list:
                    pool_hashrate[client.consensus_name] += client.hashrate
                pool_hashrate = tuple(pool_hashrate.items())
                # network hashrate
                network_hashrate = dict()
                for block in reversed(block_history_list):
                    if block['flag'] not in network_hashrate:
                        network_hashrate[block['flag']] = int(block['difficulty'] * 7158278.8)
                network_hashrate = tuple(network_hashrate.items())
                # recode pool status
                pool_status_list.append(PoolStatus(
                    ntime, workers, pool_hashrate, network_hashrate, share))
                log.debug(f"recode pool status time={ntime}")
        except DatabaseError as e:
            log.debug(f"database {e}")
        except Exception:
            log.error(f"auto pool status recode exception", exc_info=True)


async def auto_block_notify(algorithm_list: list, job_span=60):
    """auto mining job update when new block receive or job_span passed"""
    global f_enable
    consensus_list.extend(C.consensus2name[i] for i in algorithm_list)
    log.info(f"start auto notify {consensus_list}")
    while f_enable:
        try:
            data = await wait_for(block_notify_que.get(), 1)
            for algorithm in algorithm_list:
                job = await add_new_job(algorithm, force_renew=True)
                await mining_notify(job, f_clean=True)
            log.info(f"auto notify new block {data['flag']} {data['height']} {data['hash']}")
        except asyncio.TimeoutError:
            # update old jobs
            for algorithm in algorithm_list:
                try:
                    best_job = get_best_job(algorithm)
                    if best_job is None:
                        continue
                    if job_span < time() - best_job.create_time:
                        job = await add_new_job(algorithm)
                        await mining_notify(job, f_clean=True)
                        log.debug(f"auto update job {job}")
                except Exception:
                    log.error("auto update exception", exc_info=True)
            await asyncio.sleep(0.5)
        except aiohttp.ClientConnectionError:
            await asyncio.sleep(10)
        except Exception:
            log.error("auto notify exception", exc_info=True)
    log.info("close auto notify")


async def auto_notify_by_ws(dest='/public/ws'):
    """receive new block by websocket"""
    global f_enable
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(Const.REST_API + dest) as ws:
                log.info(f"connect websocket to {Const.REST_API}{dest}")
                while not ws.closed and f_enable:
                    try:
                        data: dict = await ws.receive_json(timeout=1)
                        if data['cmd'] == 'Block':
                            await block_notify_que.put(data['data'])
                            block_history_list.append(data['data'])
                        if data['cmd'] == 'TX':
                            tx_history_list.append(data['data'])
                    except asyncio.TimeoutError:
                        pass
                    except TypeError as e:
                        log.debug(f"type error {e}")
                # reconnect process
                if f_enable:
                    await asyncio.sleep(10)
                    asyncio.run_coroutine_threadsafe(auto_notify_by_ws(dest=dest), loop)
                    log.info("try to reconnect websocket")
                # close process
                try:
                    await ws.close()
                except Exception:
                    pass
                log.info("close websocket")
    except Exception:
        log.error('auto_notify_by_ws exception', exc_info=True)


def close_auto_works():
    global f_enable
    log.info("close auto notify")
    f_enable = False


__all__ = [
    "block_history_list",
    "tx_history_list",
    "consensus_list",
    "auto_distribution_recode",
    "auto_payout_system",
    "auto_pool_status_recode",
    "auto_block_notify",
    "auto_notify_by_ws",
    "close_auto_works",
]
