from bc4py_stratum_pool.config import Const, co_efficiency
from bc4py_stratum_pool.client import *
from bc4py_stratum_pool.job import *
from bc4py_stratum_pool.ask import *
from bc4py_stratum_pool.commands import *
from bc4py_stratum_pool.account import *
from bc4py_extension import address2bech
from bc4py.config import V
from aiohttp import client_exceptions
from binascii import a2b_hex
from os import urandom
from time import time
from logging import getLogger

"""Methods (client to server)
"""

log = getLogger(__name__)


async def mining_authorize(client: Client, params: list, uuid: int) -> None:
    """
    mining.authorize("username", "password")

    The result from an authorize request is usually true (successful), or false.
    The password may be omitted if the server does not require passwords.
    """
    try:
        username, password, *others = params
        hrp, version, identifier = address2bech(username)
        if not (hrp == V.BECH32_HRP and version == 0 and len(identifier) == 20):
            log.debug(f"wrong address format hrp:{hrp} ver:{version}")
            await response_success(client, False, uuid)
            return
        if username is None or password is None:
            log.debug("authorization account incorrect")
            await response_failed(client, UNAUTHORIZED_WORKER, uuid)
            return
        client.username = username
        client.password = password
        # send job
        job = get_best_job(client.algorithm)
        if job is None:
            job = await add_new_job(client.algorithm)
        async with create_db(Const.DATABASE_PATH) as db:
            cur = await db.cursor()
            account_id = await read_address2account_id(cur=cur, address=username, create_if_missing=True)
            await db.commit()
            client.account_id = account_id
        await mining_notify(job, f_clean=False)
        log.debug(f"authorize success by '{username}:{password}' id={account_id}")
        await response_success(client, True, uuid)
    except (ConnectionError, client_exceptions.ClientError) as e:
        log.debug(f"authorize failed by '{str(e)}'")
        await response_success(client, False, uuid)
    except ValueError as e:
        log.debug(f"address format error {str(e)}")
        await response_success(client, False, uuid)
    except Exception:
        log.debug("authorize failed", exc_info=True)
        await response_success(client, False, uuid)


async def mining_extranonce_subscribe(client: Client, params: list, uuid: int) -> None:
    """
    mining.extranonce.subscribe()

    Indicates to the server that the client supports the mining.set_extranonce method.
    """
    # client.f_extranonce = True
    await response_success(client, None, uuid)


async def mining_get_transactions(client: Client, params: list, uuid: int) -> None:
    """
    mining.get_transactions("job id")

    Server should send back an array with a hexdump of each transaction in the block specified for the given job id.
    """
    job_id, *others = params
    job = get_job_by_id(job_id)
    if job is None:
        await response_failed(client, JOB_NOT_FOUND, uuid)
    txs = [tx[0][::-1].hex() for tx in job.unconfirmed]
    await response_success(client, txs, uuid)


async def mining_submit(client: Client, params: list, uuid: int) -> None:
    """
    mining.submit("username", "job id", "ExtraNonce2", "nTime", "nonce")

    Miners submit shares using the method "mining.submit". Client submissions contain:
        Worker Name.
        Job ID.
        ExtraNonce2.
        nTime.
        nonce.
    Server response is result: true for accepted, false for rejected (or you may get an error with more details).
    """
    try:
        username, job_id, extranonce2, ntime, nonce, *others = params
        job_id = int.from_bytes(a2b_hex(job_id), 'big')
        extranonce2 = a2b_hex(extranonce2)
        ntime = int.from_bytes(a2b_hex(ntime), 'big')
        nonce = a2b_hex(nonce)[::-1]
        job = get_job_by_id(job_id)
        # check
        if client.username is None:
            await response_failed(client, UNAUTHORIZED_WORKER, uuid)
            return
        if client.extranonce_1 is None:
            await response_failed(client, NOT_SUBSCRIBED, uuid)
            return
        if job is None:
            await response_failed(client, JOB_NOT_FOUND, uuid)
            return
        if job.ntime != ntime:
            log.warning(f"submit different time, {job.ntime} != {ntime}")
            await response_failed(client, OTHER_UNKNOWN, uuid)
            return
        if client.algorithm not in co_efficiency:
            log.warning(f"not found algorithm in co_efficiency?")
            await response_failed(client, OTHER_UNKNOWN, uuid)
            return
        # try to generate submit data
        fixed_difficulty = min(client.diff_list) / co_efficiency[client.algorithm]
        submit_data, block, f_mined, f_shared = get_submit_data(
            job, client.extranonce_1, extranonce2, nonce, fixed_difficulty)
        if block.hash in job.submit_hashs:
            await response_failed(client, DUPLICATE_SHARE, uuid)
            return
        # try to submit work
        if f_mined or f_shared:
            client.n_accept += 1
            average_difficulty = sum(client.diff_list)/len(client.diff_list)
            client.time_works.append((time(), average_difficulty))
            job.submit_hashs.append(block.hash)
            # submit block
            if f_mined:
                pwd = str(job.algorithm)
                response = await ask_json_rpc('submitblock', [submit_data.hex()], 'user', pwd)
                if response:
                    f_mined = False
                    log.warning(f"failed mine by '{response}'")
                else:
                    log.info(f"mined yey!! {client.consensus_name} {job.height} diff={client.difficulty}")
            else:
                log.debug(f"shared work!! {client.consensus_name} {job.height} diff={client.difficulty}")
            await response_success(client, True, uuid)
            # recode share
            async with create_db(Const.DATABASE_PATH) as db:
                cur = await db.cursor()
                # how many ratio you generate hash (target/work)
                share = average_difficulty / block.difficulty / co_efficiency[client.algorithm]
                recode_hash = block.hash if f_mined else None
                payout_id = 0 if Const.PAYOUT_METHOD == 'transaction' else -1
                await insert_new_share(cur=cur, account_id=client.account_id, algorithm=client.algorithm,
                                       blockhash=recode_hash, share=share, payout_id=payout_id)
                await db.commit()
        else:
            client.n_reject += 1
            await response_failed(client, LOW_DIFFICULTY_SHARE, uuid)
    except Exception:
        log.warning("unexpected error on mining_submit", exc_info=True)
        await response_failed(client, OTHER_UNKNOWN, uuid)


async def mining_subscribe(client: Client, params: list, uuid: int) -> None:
    """
    mining.subscribe(option: "version", option: "subscription_id")

    The optional second parameter specifies a mining.notify subscription id the client wishes to resume working with
    (possibly due to a dropped connection).
    If provided, a server MAY (at its option) issue the connection the same extranonce1.
    Note that the extranonce1 may be the same (allowing a resumed connection) even if the subscription id is changed!

    The client receives a result:
        [[["mining.set_difficulty", "subscription id 1"], ["mining.notify", "subscription id 2"]], "extranonce1", extranonce2_size]

    The result contains three items:
        Subscriptions. - An array of 2-item tuples, each with a subscription type and id.
        ExtraNonce1. - Hex-encoded, per-connection unique string which will be used for creating generation transactions later.
        ExtraNonce2_size. - The number of bytes that the miner users for its ExtraNonce2 counter.
    """
    client.version = str(params[0]) if 0 < len(params) else 'unknown'
    client.subscription_id = a2b_hex(params[1]) if 1 < len(params) else None
    if client.subscription_id is None:
        # setup new subscription info
        async with create_db(Const.DATABASE_PATH) as db:
            cur = await db.cursor()
            client.extranonce_1 = urandom(4)
            client.subscription_id = await insert_new_subscription(cur=cur, extranonce=client.extranonce_1)
            await db.commit()
    else:
        # restore works from close_deque
        for old_client in reversed(closed_deque):
            if client.subscription_id != old_client.subscription_id:
                continue
            elif client.algorithm != old_client.algorithm:
                continue
            else:
                client.time_works = old_client.time_works
                client.difficulty = old_client.difficulty
                client.submit_span = old_client.submit_span
                client.extranonce_1 = old_client.extranonce_1
                client.n_accept = old_client.n_accept
                client.n_reject = old_client.n_reject
                log.debug("resume from disconnected client data")
                closed_deque.remove(old_client)
                break
        else:
            # recover subscription from database
            async with create_db(Const.DATABASE_PATH) as db:
                cur = await db.cursor()
                extranonce_1 = await read_subscription_id2extranonce(
                    cur=cur, subscription_id=client.subscription_id)
                if extranonce_1 is None:
                    # remove client info
                    client.subscription_id = None
                    raise ConnectionError('unknown subscription id')
                else:
                    client.extranonce_1 = extranonce_1
                    log.debug("resume from database")
    # notify subscription info
    extranonce_2_size = 4
    result = [
        [
            ["mining.set_difficulty", client.subscription_id.hex()],
            ["mining.notify", client.subscription_id.hex()],
        ],
        client.extranonce_1.hex(),
        extranonce_2_size
    ]
    log.debug(f"subscribe {client}")
    await response_success(client, result, uuid)


async def mining_suggest_difficulty(client: Client, params: list, uuid: int) -> None:
    """
    mining.suggest_difficulty(preferred share difficulty Number)

    Used to indicate a preference for share difficulty to the pool.
    Servers are not required to honour this request, even if they support the stratum method.
    """
    # difficulty, *others = params
    # client.difficulty = difficulty
    raise NotImplemented


async def mining_suggest_target(client: Client, params: list, uuid: int) -> None:
    """
    mining.suggest_target("full hex share target")

    Used to indicate a preference for share target to the pool, usually prior to mining.subscribe.
    Servers are not required to honour this request, even if they support the stratum method.
    """
    # target, *others = params
    # target = a2b_hex(target)
    # client.difficulty = round(DEFAULT_TARGET / float(int.from_bytes(target, 'little')), 8)
    raise NotImplemented


__all__ = [
    "mining_authorize",
    "mining_extranonce_subscribe",
    "mining_get_transactions",
    "mining_submit",
    "mining_subscribe",
    "mining_suggest_difficulty",
    "mining_suggest_target",
]
