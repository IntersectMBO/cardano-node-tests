import pytest
import urllib
from furl import furl
import json
import psycopg2


config = {}
arguments = {}

def pytest_addoption(parser):
    parser.addoption(
        "--base_url", action = "store", default = "http://localhost:3100", help = "smash base url"
    )

    parser.addoption(
        "--api_version", action="store", default = "/api/v1", help = "api version: currently v1"
    )

    parser.addoption(
        "--environment", action="store", default = "testnet", help = "mainnet, testnet or shelley_qa"
    )

def get_dbname(environment):
    if environment == 'mainnet':
        return 'cexplorer'
    elif environment == 'testnet':
        return 'testnet'  
    elif environment == 'shelley_qa':
        return 'shelley-test'
    else:
        raise NameError('NoSuchEnvironment')

@pytest.fixture(scope="session", autouse=True)
def db_session(request):
    environment = request.config.getoption("--environment")
    
    def _clear_data(cursor):
        cursor.execute('delete from reserved_pool_ticker;')
        cursor.execute('delete from delisted_pool;')
        cursor.connection.commit()

    try:
        connection = None
        connection = psycopg2.connect(dbname=get_dbname(environment), user="runner", password="")
        db_session = connection.cursor()
        _clear_data(db_session)
        yield db_session
        
    except (Exception, psycopg2.DatabaseError) as error:
        print(error)
    finally:
        if connection is not None and db_session.closed is not True:
            _clear_data(db_session)
            connection.close()

@pytest.fixture
def base_url(request):
    return request.config.getoption("--base_url")

@pytest.fixture
def api_version(request):
    return request.config.getoption("--api_version")

@pytest.fixture
def environment(request):
    return request.config.getoption("--environment")

@pytest.fixture
def get_config(pytestconfig):
    if not config:
        base_url = pytestconfig.getoption("--base_url")
        api_version = pytestconfig.getoption("--api_version")
        config["url"] = furl(base_url + api_version + "/")
        config["environment"] = pytestconfig.getoption("--environment")
    return config

@pytest.fixture
def data(get_config):
    if not arguments:
        base_url = get_config["url"]
        environment = get_config["environment"]

        ################################## SHARED DATA - FOR ALL TESTNETS #####################

        credentials = ('runner', 'password')

        arguments["smash_mainnet_url"] = {"smashURL": "https://smash.cardano-mainnet.iohk.io"}
        arguments["smash_testnet_url"] = {"smashURL": "https://smash.cardano-testnet.iohkdev.io"}
        arguments["smash_shelley_qa_url"] = {"smashURL": "https://smash.shelley-qa.dev.cardano.org"}
        arguments["smash_invalid_url"] = {"smashURL": "https://smash.invalid.dev.cardano.org"}

        arguments["credentials"] = credentials
        arguments["STATUS_ENDPOINT"] = base_url.copy().add(path='status').url
        arguments["METADATA_ENDPOINT"] = base_url.copy().add(path='metadata').url
        arguments["DELIST_ENDPOINT"] = base_url.copy().add(path='delist').url
        arguments["ENLIST_ENDPOINT"] = base_url.copy().add(path='enlist').url
        arguments["RESERVE_TICKER_ENDPOINT"] = base_url.copy().add(path='tickers').url
        arguments["ERRORS_ENDPOINT"] = base_url.copy().add(path='errors').url
        arguments["RETIRED_ENDPOINT"] = base_url.copy().add(path='retired').url
        arguments["POLICIES_ENDPOINT"] = base_url.copy().add(path='policies').url

        arguments["record_does_not_exist"] = {"code":"RecordDoesNotExist","description":"The requested record does not exist."}
        arguments["delisted_pool_already_exist"] = {'code': 'DbInsertError', 'description': 'Delisted pool already exists!'}
        
        ################################## MAINNET DATA #######################################

        if environment == 'mainnet':
            arguments["pool_hash"] = 'd9812f8d30b5db4b03e5b76cfd242db9cd2763da4671ed062be808a0'
            arguments["pool_view"] = 'pool1mxqjlrfskhd5kql9kak06fpdh8xjwc76gec76p3taqy2qmfzs5z'
            arguments["pool_metadata"] = '22cf1de98f4cf4ce61bef2c6bc99890cb39f1452f5143189ce3a69ad70fcde72'
    
            arguments["pool_json_by_hash"] = {'poolId':'d9812f8d30b5db4b03e5b76cfd242db9cd2763da4671ed062be808a0'}
            arguments["pool_json_by_view"] ={'poolId':'pool1mxqjlrfskhd5kql9kak06fpdh8xjwc76gec76p3taqy2qmfzs5z'}
            arguments["pool_with_errors"] = '88508d3e22a0045017318fd21462ad7874e8f9651b6bee28b81f8793'
            arguments["pool_is_delisted"] = 'Pool d9812f8d30b5db4b03e5b76cfd242db9cd2763da4671ed062be808a0 is delisted'

            arguments["expected_metadata"] = {
                "name": "Input Output Global (IOHK) - 1",
                "ticker": "IOG1",
                "homepage": "https://iohk.io",
                "description": "Our mission is to provide economic identity to the billions of people who lack it. IOHK will not use the IOHK ticker."
            }

            arguments["unregistered_pools_examples"] = [
                {'poolId': 'fc48e5d44e1a3f662d0225a765c6eb02e2379407d361e9b317e245b3'},
                {'poolId': 'f5aa6abc4ef18e02492d7295baa7d152944a65c8cd0cdcd99b4fb37a'}, 
                {'poolId': '4f0fa098d08e0b044adb0b8c76ab8dc59742149847bbb0b5272d7efc'}
            ]

        ################################## SHELLEY_QA DATA ####################################

        if environment == 'shelley_qa':
            arguments["pool_hash"] = '8517fa7042cb9494818861c53c87780b4975c0bd402e3ed85168aa66'
            arguments["pool_view"] = 'pool1s5tl5uzzew2ffqvgv8znepmcpdyhts9agqhrakz3dz4xvfs049l'
            arguments["pool_metadata"] = '4b2221a0ac0b0197308323080ba97e3e453f8625393d30f96eebe0fca4cb7334'
    
            arguments["pool_json_by_hash"] = {'poolId':'8517fa7042cb9494818861c53c87780b4975c0bd402e3ed85168aa66'}
            arguments["pool_json_by_view"] ={'poolId':'pool1s5tl5uzzew2ffqvgv8znepmcpdyhts9agqhrakz3dz4xvfs049l'}
            arguments["pool_with_errors"] = '0f82d55b5a5b8a1f103e03b59bd2754cd900efed3e0d62137c6edcb9'
            arguments["pool_is_delisted"] = 'Pool 8517fa7042cb9494818861c53c87780b4975c0bd402e3ed85168aa66 is delisted'

            arguments["expected_metadata"] = {
                "name": "IOG 1",
                "ticker": "IOG1",
                "homepage": "https://iohk.io",
                "description": "IOG Testnet Pool"
            }

            arguments["unregistered_pools_examples"] = [
                {'poolId': 'dba7b19a960d9e21748aaac28388432018efd80957ce09df2fb91953'}
            ]

        ################################## TESTNET DATA ##################################

        if environment == 'testnet':
            arguments["pool_hash"] = '48f2c367cfe81cac6687c3f7c26613edfe73cd329402aa5cf493bb61'
            arguments["pool_view"] = 'pool1frevxe70aqw2ce58c0muyesnahl88nfjjsp25h85jwakzgd2g2l'
            arguments["pool_metadata"] = '4b2221a0ac0b0197308323080ba97e3e453f8625393d30f96eebe0fca4cb7334'

            arguments["pool_json_by_hash"] = {'poolId':'48f2c367cfe81cac6687c3f7c26613edfe73cd329402aa5cf493bb61'}
            arguments["pool_json_by_view"] = {'poolId':'pool1frevxe70aqw2ce58c0muyesnahl88nfjjsp25h85jwakzgd2g2l'}
            arguments["pool_with_errors"] = 'be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853'
            arguments["pool_is_delisted"] = 'Pool 48f2c367cfe81cac6687c3f7c26613edfe73cd329402aa5cf493bb61 is delisted'

            arguments["expected_metadata"] = {
                "name": "IOG 1",
                "ticker": "IOG1",
                "homepage": "https://iohk.io",
                "description": "IOG Testnet Pool"
            }

            arguments["unregistered_pools_examples"] = [
                {'poolId': '002c501d063cf552144e58ea9a85c8d156b3d7c7a498e52a50cf546c'},
                {'poolId': '00cd5fd9cbf0b9535f804f59da4666859afa38e5ca7729a3172efe36'}, 
                {'poolId': '0192e6835f8613b1a47084d800ee3d2a0931a334e5e63dd80c447c15'}
            ]
    
    return arguments
