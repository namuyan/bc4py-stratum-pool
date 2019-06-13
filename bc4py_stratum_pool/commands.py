from bc4py_stratum_pool.client import *
from bc4py_stratum_pool.job import *
from bc4py_extension import sha256d_hash
from more_itertools import chunked
from logging import getLogger

log = getLogger(__name__)

"""Methods (server to client)
"""


async def client_show_message(client: Client):
    """
    client.show_message("human-readable message")

    The client should display the message to its user in some reasonable way.
    """
    await client.send('client.show_message', ["hello world message"], None)


async def mining_notify(job: Job, f_clean=False):
    """
    mining.notify(...)

    Fields in order:
        Job ID      This is included when miners submit a results so work can be matched with proper transactions.
        Hash of previous block    Used to build the header.
        Generation transaction (part 1)   The miner inserts ExtraNonce1 and ExtraNonce2 after this section of the transaction data.
        Generation transaction (part 2)   The miner appends this after the first part of the transaction data and the two ExtraNonce values.
        List of merkle branches    The generation transaction is hashed against the merkle branches to build the final merkle root.
        Bitcoin block version    Used in the block header.
        nBits. The encoded network difficulty     Used in the block header.
        nTime. The current time     nTime rolling should be supported, but should not increase faster than actual time.
        Clean Jobs    If true, miners should abort their current work and immediately use the new job. If false, they can still use the current job, but should move to the new one after exhausting the current nonce range.
    """
    unconfirmed = [txhash for txhash, _ in job.unconfirmed]
    params = [
        job.job_id.to_bytes(4, 'big').hex(),
        swap_pre_processed_sha2(job.previous_hash).hex(),
        job.coinbase1.hex(),
        job.coinbase2.hex(),
        [txhash.hex() for txhash in pre_merkleroot(unconfirmed)],
        job.version.to_bytes(4, 'big').hex(),
        job.bits.hex(),
        job.ntime.to_bytes(4, 'big').hex(),
        f_clean,
    ]
    count = await broadcast_clients('mining.notify', params, job.algorithm)
    log.debug(f"broadcast {count} clients")


async def client_reconnect(client: Client, host, port):
    """
    client.reconnect("hostname", port, waittime)

    The client should disconnect, wait waittime seconds (if provided),
    then connect to the given host/port (which defaults to the current server).
    Note that for security purposes, clients may ignore such requests if the destination is not the same or similar.
    """
    await client.send("client.reconnect", [host, port, 5], None)


async def mining_set_difficulty(client: Client):
    """
    mining.set_difficulty(difficulty)

    The server can adjust the difficulty required for miner shares with the "mining.set_difficulty" method.
    The miner should begin enforcing the new difficulty on the next job received.
    Some pools may force a new job out when set_difficulty is sent, using clean_jobs to force the miner
    to begin using the new difficulty immediately.
    """
    await client.send('mining.set_difficulty', [client.difficulty], None)


async def mining_set_extranonce(client: Client):
    """
    mining.set_extranonce("extranonce1", extranonce2_size)

    These values, when provided, replace the initial subscription values beginning with the next mining.notify job.
    """
    raise NotImplemented


def swap_pre_processed_sha2(h):
    """pre-processed SHA-2 chunks"""
    r = b''
    for i in reversed(range(0, 32, 4)):
        r += h[i:i+4]
    return r[::-1]


def pre_merkleroot(tree: list):
    """generate merkleroot without coinbase tx"""
    tree = tree.copy()
    mask_index = 1
    while True:
        if len(tree) > mask_index:
            if (len(tree) - mask_index) % 2 == 1:
                tree.append(tree[-1])
            else:
                new_tree = tree[:mask_index]
                new_tree.extend(
                    sha256d_hash(a + b) for a, b in chunked(tree[mask_index:], 2)
                )
                tree = new_tree
                mask_index += 1
        else:
            break
    return tree


def test_pre_merkleroot():
    from binascii import a2b_hex
    # block height: 1580725
    # block hash  : 00000003b59fa6638b21cc8bbf9c96b8a72f882440df5a13ce9ede18e9481ae9
    original = [
        '41091d1f9b4f2a4f562c4d24793a46d55c915f25e24342bf1918540d317c4c42',
        '281324435c35f53301df50ed9b3af215247f0ab74c35d5df5177d439e0fc87ec',
        'a2500f840f2d53f24dad53b272404fca16798d06e20cba608ea1c0e17e73efd3',
        '1ad525dd7674f427482e9b3a1e57084ca85dc46c4c90d96388a17801f056d65c',
        'a7f52fb50483f77c297e5ab30519102d1a8499412ba6f8c184bd79cb24034705',
    ]
    expect = [
        "41091d1f9b4f2a4f562c4d24793a46d55c915f25e24342bf1918540d317c4c42",
        "a1bc6f3b480c62ebc04ddfc1e58967e77e56a1ace34c73796008fdba8c2024ab",
        "2532aed76199db600abf31e120c4a70e0405d475f17226553a991d6d54acb3d6",
    ]
    original = [a2b_hex(tx) for tx in original]
    result = pre_merkleroot(original)
    result = [tx.hex() for tx in result]
    assert result == expect, f"{result} != {expect}"


__all__ = [
    "client_reconnect",
    "client_show_message",
    "mining_notify",
    "mining_set_difficulty",
    "mining_set_extranonce",
]
