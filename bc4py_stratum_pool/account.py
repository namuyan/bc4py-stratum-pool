from aiocontext import async_contextmanager
from aiosqlite import connect, Connection, Cursor
from typing import Optional, List, Dict
from logging import getLogger, INFO
from binascii import a2b_hex
from os import urandom
from time import time

log = getLogger(__name__)
getLogger('aiosqlite').setLevel(INFO)


@async_contextmanager
async def create_db(path, strict=False) -> Connection:
    """
    account database connector
    TODO: will be duplicate with bc4py's

    f_strict:
        Phantom read sometimes occur on IMMEDIATE, avoid it by EXCLUSIVE.

    journal_mode: (Do not use OFF mode)
        DELETE: delete journal file at end of transaction
        TRUNCATE: set journal file size to 0 at the end of transaction
        PERSIST: disable headers at end of transaction
        MEMORY: save journal file on memory
        WAL: use a write-ahead log instead of a rollback journal

    synchronous:
        FULL: ensure that all content is safely written to the disk.
        EXTRA: provides additional durability if the commit is followed closely by a power loss.
        NORMAL: sync at the most critical moments, but less often than in FULL mode.
        OFF: without syncing as soon as it has handed data off to the operating system.
    """
    conn = await connect(path, timeout=120)

    # cashe size, default 2000
    # conn.execute("PRAGMA cache_size = 4000")

    # journal mode
    await conn.execute("PRAGMA journal_mode = WAL")
    conn.isolation_level = 'EXCLUSIVE' if strict else 'IMMEDIATE'

    # synchronous mode
    await conn.execute("PRAGMA synchronous = NORMAL")

    # manage close process with contextmanager
    try:
        yield conn
    finally:
        await conn.close()


async def first_init_database(path):
    """
    initialize database

    account: get address by account_id
    share: get share with time range
    transaction: get payout transaction history
    """
    try:
        async with create_db(path) as db:
            cur = await db.cursor()
            await cur.execute("""
            CREATE TABLE IF NOT EXISTS `account` (
            `id` INTEGER PRIMARY KEY,
            `address` TEXT NOT NULL,
            `time` INTEGER NOT NULL
            )""")
            await cur.execute("""
            CREATE TABLE IF NOT EXISTS `subscription` (
            `id` INTEGER PRIMARY KEY,
            `extranonce` BLOB NOT NULL,
            `time` INTEGER NOT NULL
            )""")
            await cur.execute("""
            CREATE TABLE IF NOT EXISTS `share` (
            `time` REAL PRIMARY KEY,
            `account_id` INTEGER NOT NULL,
            `algorithm` INTEGER NOT NULL,
            `blockhash` BLOB,
            `share` REAL NOT NULL,
            `payout_id` INTEGER NOT NULL
            )""")
            await cur.execute("""
            CREATE TABLE IF NOT EXISTS `transaction` (
            `id` INTEGER PRIMARY KEY,
            `txhash` BLOB NOT NULL,
            `amount` INTEGER NOT NULL,
            `begin` INTEGER NOT NULL,
            `end` INTEGER NOT NULL,
            `time` INTEGER NOT NULL
            )""")
            await cur.execute("CREATE INDEX IF NOT EXISTS `address_index` ON `account` (`address`)")
            await cur.execute("CREATE INDEX IF NOT EXISTS `txhash_index` ON `transaction` (`txhash`)")
            await cur.execute("CREATE INDEX IF NOT EXISTS `time_index` ON `transaction` (`time`)")
    except Exception:
        log.error("database init exception", exc_info=True)
    log.info("finish init database")


async def cleanup_database(path, past=60*24*60):
    """cleanup old data from database"""
    try:
        async with create_db(path) as db:
            cur = await db.cursor()
            # auto clean data
            time_limit = int(time()) - past
            await cur.execute("""
            DELETE FROM `subscription` WHERE `time` < ?
            """, (time_limit,))
            await cur.execute("""
            DELETE FROM `share` WHERE `time` < ?
            """, (time_limit,))
            await db.commit()
    except Exception:
        log.error("database cleanup exception", exc_info=True)


"""account
"""


async def read_address2account_id(cur: Cursor, address, create_if_missing=False) -> int:
    """get id from address, create if missing as option"""
    await cur.execute("""
    SELECT `id` FROM `account` WHERE `address`=?
    """, (address,))
    account_id = await cur.fetchone()
    if account_id is None:
        if not create_if_missing:
            raise DatabaseError('not found related account')
        return await insert_new_account(cur, address)
    else:
        return account_id[0]


async def read_account_id2address(cur: Cursor, account_id) -> Optional[str]:
    """get address by account_id"""
    await cur.execute("""
    SELECT `address` FROM `account` WHERE `id`=?
    """, (account_id,))
    address = await cur.fetchone()
    if address is None:
        return None
    else:
        return address[0]


async def insert_new_account(cur: Cursor, address) -> int:
    """create new account by address"""
    await cur.execute("""
        INSERT INTO `account` (`address`, `time`) VALUES (?, ?)
        """, (address, int(time())))
    await cur.execute("SELECT last_insert_rowid()")
    account_id = await cur.fetchone()
    return account_id[0]


"""subscribe
"""


async def read_subscription_id2extranonce(cur: Cursor, subscription_id: bytes):
    """get extranonce_1 from subscription_id"""
    top_id = subscription_id[26:32]
    await cur.execute("""
    SELECT `extranonce` FROM `subscription` WHERE `id`=?
    """, (int.from_bytes(top_id, 'big'),))
    extranonce = await cur.fetchone()
    if extranonce is None:
        return None
    return extranonce[0]


async def insert_new_subscription(cur: Cursor, extranonce):
    """recode extranonce and subscription_id"""
    top_id = urandom(6)
    await cur.execute("""
    INSERT INTO `subscription` (`id`, `extranonce`, `time`) VALUES (?,?,?)
    """, (int.from_bytes(top_id, 'big'), extranonce, int(time())))
    # 26 + 6 = 32 bytes
    return a2b_hex('deadbeefcafe00000000000000000000000000000000000000ff') + top_id


"""share
"""


async def read_total_unpaid_shares(cur: Cursor, begin, end, f_raise=True) -> float:
    """get total un_payed works from begin to end"""
    await cur.execute("""
    SELECT SUM(`share`) FROM `share` WHERE ? <= `time` AND `time` < ? AND `payout_id` < 1
    """, (begin, end))
    share = await cur.fetchone()
    if share[0] is None:
        if f_raise:
            DatabaseError(f"no total share info {begin} -> {end}")
        return 0.0
    return share[0]


async def read_account_unpaid_shares(cur: Cursor, begin, end, account_id) -> float:
    """get account's work from begin to end"""
    await cur.execute("""
    SELECT SUM(`share`) FROM `share` WHERE ? <= `time` AND `time` < ? AND `payout_id` < 1 AND `account_id`=?
    """, (begin, end, account_id))
    data = await cur.fetchone()
    if data[0] is None:
        DatabaseError(f"no account share info id={account_id}")
    return data[0]


async def read_distribution_shares(cur: Cursor, begin, end, algorithm) -> Dict[int, float]:
    """get each account's mining share"""
    await cur.execute("""
    SELECT `account_id`, SUM(`share`) FROM `share`
    WHERE ? <= `time` AND `time` < ? AND `algorithm` = ?
    GROUP BY `account_id`
    """, (begin, end, algorithm))
    data = await cur.fetchall()
    dist = {account_id: share for account_id, share in data}
    return dist


async def read_related_accounts(cur: Cursor, begin, end) -> List[int]:
    """get unique account's id related share"""
    await cur.execute("""
    SELECT DISTINCT `account_id` FROM `share` WHERE ? <= `time` AND `time` < ?
    """, (begin, end))
    data = await cur.fetchall()
    return [account_id for (account_id,) in data]


async def read_related_blockhash(cur: Cursor, begin, end) -> List[bytes]:
    """get unique account's id related share"""
    await cur.execute("""
    SELECT DISTINCT `blockhash` FROM `share` WHERE ? <= `time` AND `time` < ?
    """, (begin, end))
    data = await cur.fetchall()
    return [blockhash for (blockhash,) in data]


async def read_last_unpaid_time(cur: Cursor) -> Optional[float]:
    """get last paid share time"""
    await cur.execute("""
    SELECT `time`, `payout_id` FROM `share` ORDER BY `time` DESC
    """)
    before_time = None
    async for ntime, payout_id in cur:
        if payout_id != 0:
            return before_time
        before_time = ntime
    if before_time is None:
        raise DatabaseError('no share recoded')
    return before_time


async def iter_latest_mined_shares(cur: Cursor):
    await cur.execute("""
    SELECT `time`, `blockhash`, `payout_id` FROM `share` ORDER BY `time` DESC
    """)
    async for ntime, blockhash, payout_id in cur:
        if payout_id != 0:
            break
        if blockhash is not None:
            yield ntime, blockhash


async def insert_new_share(cur: Cursor, account_id, algorithm, blockhash, share, payout_id):
    """recode account's submit share"""
    await cur.execute("""
    INSERT INTO `share` (
    `time`, `account_id`, `algorithm`, `blockhash`, `share`, `payout_id`
    ) VALUES (?,?,?,?,?,?)
    """, (time(), account_id, algorithm, blockhash, share, payout_id))


async def update_shares_as_paid(cur: Cursor, payout_id, begin, end, accounts):
    """mark paid shares"""
    for uuid in accounts:
        assert isinstance(uuid, int)
    await cur.execute("""
    UPDATE `share` SET `payout_id`=?
    WHERE ? <= `time` AND `time` < ? AND `payout_id`=0 AND `account_id` IN (%s)
    """ % ', '.join(map(str, accounts)), (payout_id, begin, end))


async def revert_paid_shares(cur: Cursor, begin, end, payout_id):
    """revert paid shares"""
    await cur.execute("""
    UPDATE `share` SET `payout_id`=0 WHERE ? <= `time` AND `time` < ? AND `payout_id`=?
    """, (begin, end, payout_id))


"""transaction
"""


async def read_payout2txhash(cur: Cursor, payout_id):
    """get txhash by payout id"""
    await cur.execute("""
    SELECT `txhash` FROM `transaction` WHERE `id`=?
    """, (payout_id,))
    txhash = await cur.fetchone()
    if txhash is None:
        raise DatabaseError(f"Not found payout id={payout_id}")
    return txhash[0]


async def read_txhash2payout(cur: Cursor, txhash):
    """get payout id from txhash"""
    await cur.execute("""
    SELECT `id` FROM `transaction` WHERE `txhash`=?
    """, (txhash,))
    payout_id =await cur.fetchone()
    if payout_id is None:
        raise DatabaseError(f"not found txhash {txhash.hex()}")
    return payout_id[0]


async def read_last_paid_txhash(cur: Cursor):
    """get last paid txhash"""
    await cur.execute("""
        SELECT `txhash` FROM `transaction` ORDER BY `id` DESC
        """)
    txhash = await cur.fetchone()
    if txhash is None:
        return None
    return txhash[0]


async def iter_payout_transactions(cur: Cursor):
    """list all payout transactions"""
    await cur.execute("""
    SELECT `id`, `txhash`, `amount`, `begin`, `end`, `time` FROM `transaction` ORDER BY `id` DESC
    """)
    async for data in cur:
        yield data


async def insert_new_transaction(cur: Cursor, txhash, amount, begin, end) -> int:
    await cur.execute("""
    INSERT INTO `transaction` (`txhash`, `amount`, `begin`, `end`, `time`)
    VALUES (?, ?, ?, ?, ?)
    """, (txhash, amount, begin, end, int(time())))
    return cur.lastrowid


class DatabaseError(Exception):
    pass


__all__ = [
    "create_db",
    "first_init_database",
    "read_address2account_id",
    "read_account_id2address",
    "insert_new_account",
    "read_subscription_id2extranonce",
    "insert_new_subscription",
    "read_total_unpaid_shares",
    "read_account_unpaid_shares",
    "read_distribution_shares",
    "read_related_accounts",
    "read_related_blockhash",
    "read_last_unpaid_time",
    "iter_latest_mined_shares",
    "insert_new_share",
    "update_shares_as_paid",
    "revert_paid_shares",
    "read_payout2txhash",
    "read_txhash2payout",
    "read_last_paid_txhash",
    "iter_payout_transactions",
    "insert_new_transaction",
    "DatabaseError",
]
