bc4py stratum pool
====
stratum pool program for [bc4py](http://github.com/namuyan/bc4py)

specification
----
* Asynchronous I/O
* auto schedule difficulty
* BTC like stratum protocol
* Auto payout system
* will work by cpuminer/cgminer/sgminer/ccminer

environment
----
* for windows/linux
* Python **3.6+**
* Rust nightly

install
----
Premise: The explanation is based by using Ubuntu or other Linux
Premise: You have finished setup bc4py node and full synced
```bash
# into blockchain-py
cd ~/blockchain-py/
 
# download bc4py-stratum-pool program
git clone https://github.com/namuyan/bc4py-stratum-pool
 
# install requirements
pip3 install --user -r bc4py-stratum-pool/requirements.txt
 
# copy pool program to same folder with bc4py source
cp -r bc4py-stratum-pool/bc4py_stratum_pool ./
rm -r bc4py-stratum-pool
 
# write example start scrypt
cat << EOS > start_pool.py
#!/user/env python3
# -*- coding: utf-8 -*-
from bc4py_stratum_pool.config import Const
from bc4py_stratum_pool.autowork import *
from bc4py_stratum_pool.stratum import stratum_server
from bc4py_stratum_pool.web import web_server
from bc4py_stratum_pool.account import first_init_database
from bc4py.for_debug import set_logger
from asyncio import get_event_loop, run_coroutine_threadsafe
from bc4py.config import C
import logging
 
loop = get_event_loop()
log = logging.getLogger(__name__)
 
 
def main():
    set_logger(logging.DEBUG)
    # list of pool algorithms
    algorithm_list = [
        C.BLOCK_YES_POW,
        C.BLOCK_X16S_POW,
        C.BLOCK_X11_POW,
    ]
    # hostname
    Const.HOST_NAME = 'pool.example.com'
    # account database control
    run_coroutine_threadsafe(first_init_database(Const.DATABASE_PATH), loop)
    # auto payout
    run_coroutine_threadsafe(auto_payout_system(min_confirm=100), loop)
    # pool status recode
    run_coroutine_threadsafe(auto_pool_status_recode(), loop)
    # auto notify new block by websocket
    run_coroutine_threadsafe(auto_block_notify(algorithm_list), loop)
    run_coroutine_threadsafe(auto_notify_by_ws(), loop)
    # all mining ports (port, algorithm, difficulty)
    run_coroutine_threadsafe(stratum_server(5000, C.BLOCK_YES_POW, 0.01), loop)
    run_coroutine_threadsafe(stratum_server(5001, C.BLOCK_YES_POW, 0.1), loop)
    run_coroutine_threadsafe(stratum_server(5002, C.BLOCK_YES_POW, 1.0, variable_diff=False), loop)
    run_coroutine_threadsafe(stratum_server(5003, C.BLOCK_X16S_POW, 0.1), loop)
    run_coroutine_threadsafe(stratum_server(5004, C.BLOCK_X16S_POW, 1.0), loop)
    run_coroutine_threadsafe(stratum_server(5005, C.BLOCK_X16S_POW, 32.0, variable_diff=False), loop)
    run_coroutine_threadsafe(stratum_server(5006, C.BLOCK_X11_POW, 0.1), loop)
    run_coroutine_threadsafe(stratum_server(5007, C.BLOCK_X11_POW, 1.0), loop)
    run_coroutine_threadsafe(stratum_server(5008, C.BLOCK_X11_POW, 32.0, variable_diff=False), loop)
    # web server
    run_coroutine_threadsafe(web_server(8080), loop)
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        loop.close()
 
 
if __name__ == '__main__':
    main()
EOS
 
# start scrypt
python3 start_pool.py
```

note
----
* Install rust nightly
```bash
# require Rust nightly
curl https://sh.rustup.rs -sSf | sh -s -- --default-toolchain nightly
```
* [How to setup testnet node](https://hackmd.io/s/SJwtbBpI4)

thanks
----
* [octicons](https://octicons.github.com/)
* [Jquery](https://jquery.com/download/)
* [Bootstrap4.3.1](https://github.com/twbs/bootstrap)
* [Chart.js](https://www.chartjs.org/)
* [aiosqlite](https://github.com/jreese/aiosqlite)
* [aiohttp](https://aiohttp.readthedocs.io/en/stable/)
* [aiohttp-jinja2](https://github.com/aio-libs/aiohttp-jinja2)
* [jinja2-time](https://github.com/hackebrot/jinja2-time)
* [Jinja2](http://jinja.pocoo.org/docs/2.10/)
* [expiringdict](https://github.com/mailgun/expiringdict)
* [asyncio-contextmanager](https://github.com/sashgorokhov/asyncio-contextmanager)
* [bc4py](http://github.com/namuyan/bc4py)
* [bc4py-extension](http://github.com/namuyan/bc4py_extension)

licence
---
[MIT](https://github.com/namuyan/bc4py-stratum-pool/blob/master/LICENSE)

Author
---
[@namuyan_mine](http://twitter.com/namuyan_mine/)
