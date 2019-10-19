from bc4py_stratum_pool.config import *
from bc4py_stratum_pool.ask import *
from bc4py_stratum_pool.autowork import *
from bc4py_stratum_pool.client import client_list
from bc4py_stratum_pool.stratum import stratum_list
from bc4py.config import C
from logging import getLogger
from aiohttp.web import Request
from aiohttp import web
from jinja2 import FileSystemLoader, FileSystemBytecodeCache
import aiohttp_jinja2
from time import time
import asyncio
import os


loop = asyncio.get_event_loop()
log = getLogger(__name__)
DISABLE_EXPLORER = False


@aiohttp_jinja2.template('index.html')
async def page_index(request: Request):
    return {
        'title': 'main page'
    }


@aiohttp_jinja2.template('started.html')
async def page_started(request: Request):
    return {
        'title': 'getting started',
        'hostname': Const.HOST_NAME,
        'stratum': stratum_list
    }


@aiohttp_jinja2.template('dashboard.html')
async def page_dashboard(request: Request):
    if len(block_history_list) == 0 or len(pool_status_list) == 0:
        return {
            'title': 'dashboard',
            'wait_for_info': True}
    # enable display
    newest = pool_status_list[-1]
    data = {
        'title': 'dashboard',
        'workers': len(client_list),
        'pool_hashrate': newest.pool_hashrate,
        'network_hashrate': newest.network_hashrate,
        'best_block': block_history_list[-1],
        'pool_status_list': pool_status_list,
        'consensus_list': consensus_list,
    }
    # add distribution
    if 0 < len(distribution_list):
        distribution = dict()
        for dist in reversed(distribution_list):
            if dist.algorithm not in distribution:
                distribution[dist.algorithm] = dist.distribution
        distribution = [
            (C.consensus2name[algorithm], dist)
            for algorithm, dist in distribution.items()]
        data['distribution'] = distribution
    return data


@aiohttp_jinja2.template('explorer.html')
async def page_explorer(request: Request):
    # At the beginning, history list is empty
    if not DISABLE_EXPLORER and 'blockhash' in request.query:
        try:
            blockhash = request.query['blockhash'].lower()
            int(blockhash, 16)  # check
            params = {'hash': blockhash, 'txinfo': 'true'}
            block = await ask_get('/public/getblockbyhash', params)
            if isinstance(block, dict):
                best_height = block_history_list[-1]['height'] if 0 < len(block_history_list) else None
                block['title'] = 'block info'
                return {
                    'title': 'explorer -block-',
                    'block_info': block,
                    'best_height': best_height}
            else:
                raise BlockExplorerError(block)
        except (ValueError, TypeError, IndexError, ConnectionError) as e:
            raise BlockExplorerError(e)
    elif not DISABLE_EXPLORER and 'height' in request.query:
        try:
            height = int(request.query['height'])
            params = {'height': height, 'txinfo': 'true'}
            block = await ask_get('/public/getblockbyheight', params)
            if isinstance(block, dict):
                best_height = block_history_list[-1]['height'] if 0 < len(block_history_list) else None
                block['title'] = 'block info'
                return {
                    'title': 'explorer -block-',
                    'block_info': block,
                    'best_height': best_height}
            else:
                raise BlockExplorerError(block)
        except (ValueError, TypeError, IndexError, ConnectionError) as e:
            raise BlockExplorerError(e)
    elif not DISABLE_EXPLORER and 'txhash' in request.query:
        try:
            txhash = request.query['txhash'].lower()
            int(txhash, 16)  # check
            params = {'hash': txhash}
            tx = await ask_get('/public/gettxbyhash', params)
            if isinstance(tx, dict):
                best_height = block_history_list[-1]['height'] if 0 < len(block_history_list) else None
                return {
                    'title': 'explorer -tx-',
                    'tx_info': tx,
                    'best_height': best_height}
            else:
                raise BlockExplorerError(tx)
        except (ValueError, TypeError, IndexError, ConnectionError) as e:
            raise BlockExplorerError(e)
    else:
        return {
            'title': 'explorer -randing-',
            'blocks': list(reversed(block_history_list)),
            'txs': list(reversed(tx_history_list)),
            'time': int(time())}


@aiohttp_jinja2.template('connection.html')
async def page_connection(request: Request):
    data = [
        {
            'version': client.version,
            'username': client.username,
            'consensus': client.consensus_name,
            'difficulty': client.difficulty,
            'hashrate': client.hashrate_str,
            'accept': client.n_accept,
            'reject': client.n_reject,
        }
        for client in client_list]
    return {
        'title': 'connection',
        'data': data
    }


@aiohttp_jinja2.template('terms.html')
async def page_terms(request: Request):
    return {'title': 'Terms&Conditions'}


async def error_middleware(app, handler):
    async def middleware_handler(request: Request):
        try:
            return await handler(request)
        except BlockExplorerError as e:
            context = {
                'title': 'block explorer error',
                'status': 400,  # bad request
                'message': str(e)}
            return aiohttp_jinja2.render_template('error.html', request, context)
        except web.HTTPException as e:
            context = {
                'title': 'error page',
                'status': e.status,
                'message': e.text}
            return aiohttp_jinja2.render_template('error.html', request, context)
    return middleware_handler


async def web_server(port, host='0.0.0.0', ssl_context=None):
    """http web server"""
    try:
        app = web.Application(middlewares=[error_middleware])
        web_root_dir = os.path.split(os.path.abspath(__file__))[0]
        cashe_path = os.path.join(web_root_dir, '.cashe')
        static_path = os.path.join(web_root_dir, 'templates', 'static')
        if not os.path.exists(cashe_path):
            os.mkdir(cashe_path)
        aiohttp_jinja2.setup(
            app=app,
            loader=FileSystemLoader(os.path.join(web_root_dir, 'templates')),
            bytecode_cache=FileSystemBytecodeCache(directory=cashe_path, pattern='%s.cashe'),
            extensions=['jinja2_time.TimeExtension'],
        )
        # add routes
        app.router.add_get('/', page_index)
        app.router.add_get('/index.html', page_index)
        app.router.add_get('/started.html', page_started)
        app.router.add_get('/dashboard.html', page_dashboard)
        app.router.add_get('/explorer.html', page_explorer)
        app.router.add_get('/connection.html', page_connection)
        app.router.add_get('/terms.html', page_terms)
        app.router.add_static('/static', static_path)
        # start server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=port, ssl_context=ssl_context)
        await site.start()
        log.info(f"start web server {host}:{port} ssl={bool(ssl_context)}")
    except Exception:
        log.error("web server exception", exc_info=True)


class BlockExplorerError(Exception):
    pass


__all__ = [
    "web_server",
]
