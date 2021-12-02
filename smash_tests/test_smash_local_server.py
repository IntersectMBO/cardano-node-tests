import pytest
import json
from furl import furl
import requests
from http import HTTPStatus as HTTP_STATUS

# Utils

def _validate_status_and_content_type(response, status_code):  
    assert response.status_code == status_code
    assert response.headers["Content-Type"] == "application/json;charset=utf-8"


def ticker_must_be_between_3_and_5_chars_but_has(ticker): 
    return '"ticker" must have at least 3 and at most 5 characters, but it has {} characters.'.format(len(ticker))


def ticker_already_reserved(ticker): 
    return '{"code":"TickerAlreadyReserved","description":"Ticker name %s is already reserved"}'  % ticker


def get_delisted_pools_from_db(db_session):
    delisted_pools = set()

    db_session.execute("SELECT hash_raw FROM delisted_pool")
    pool = db_session.fetchone()

    while pool is not None:
        delisted_pools.add(pool[0].hex())
        pool = db_session.fetchone()

    return delisted_pools


# Tests

def test_health_status(data):
    response = requests.get(data["STATUS_ENDPOINT"])
    _validate_status_and_content_type(response, HTTP_STATUS.OK)
    assert response.json() == {"status":"OK","version":"12.0.0"}


def test_fetch_metadata_by_pool_hash(data):
    metadata_url = furl(data["METADATA_ENDPOINT"]).add(path=data["pool_hash"]).add(path=data["pool_metadata"]).url

    response = requests.get(metadata_url)
    _validate_status_and_content_type(response, HTTP_STATUS.OK)
    assert response.json() == data["expected_metadata"]   
    

def test_fetch_metadata_by_pool_view(data):
    metadata_url = furl(data["METADATA_ENDPOINT"]).add(path=data["pool_view"]).add(path=data["pool_metadata"]).url

    response = requests.get(metadata_url)
    _validate_status_and_content_type(response, HTTP_STATUS.OK)
    assert response.json() == data["expected_metadata"]


def test_delist_by_pool_hash(data):
    response = requests.patch(data["DELIST_ENDPOINT"], json = data["pool_json_by_hash"], auth = data["credentials"])
    _validate_status_and_content_type(response, HTTP_STATUS.OK)
    assert response.json() == data["pool_json_by_hash"]
    # There is NO log message on SMASH server side informing that some pool was added to blacklist

    response_after_delisting = requests.get(furl(data["METADATA_ENDPOINT"]).add(path=data["pool_hash"]).add(path=data["pool_metadata"]).url)
    _validate_status_and_content_type(response, HTTP_STATUS.OK)
    assert response_after_delisting.text == data["pool_is_delisted"]


def test_delist_already_delisted_pool_by_pool_view(data):
    response = requests.patch(data["DELIST_ENDPOINT"], json = data["pool_json_by_view"], auth = data["credentials"])

    assert response.status_code == HTTP_STATUS.BAD_REQUEST
    # No "Content-Type" set
    assert response.json() == data["delisted_pool_already_exist"]


def test_whitelist_by_pool_hash(data):
    response = requests.patch(data["ENLIST_ENDPOINT"], json = data["pool_json_by_hash"], auth = data["credentials"])

    _validate_status_and_content_type(response, HTTP_STATUS.OK)
    assert response.json() == data["pool_json_by_hash"]

    response_after_whitelisting = requests.get(furl(data["METADATA_ENDPOINT"]).add(path=data["pool_view"]).add(path=data["pool_metadata"]).url)
    _validate_status_and_content_type(response_after_whitelisting, HTTP_STATUS.OK)
    assert response_after_whitelisting.json() == data["expected_metadata"]


def test_whitelist_already_whitelisted_pool_by_pool_view(data):
    response = requests.patch(data["ENLIST_ENDPOINT"], json = data["pool_json_by_view"], auth = data["credentials"])

    assert response.status_code == HTTP_STATUS.NOT_FOUND
    # No "Content-Type" set
    assert response.json() == data["record_does_not_exist"]


def test_reserve_ticker_no_characters(data):
    empty_ticker = ""
    ticker_url = furl(data["RESERVE_TICKER_ENDPOINT"]).add(path=empty_ticker).url

    response = requests.post(ticker_url, json = data["pool_json_by_view"], auth = data["credentials"])
    assert response.status_code == HTTP_STATUS.METHOD_NOT_ALLOWED
    # No "Content-Type" set
    assert response.text == ""


def test_reserve_ticker_too_short(data):
    ticker_too_short = "QA"
    ticker_url = furl(data["RESERVE_TICKER_ENDPOINT"]).add(path=ticker_too_short).url

    response = requests.post(ticker_url, json = data["pool_json_by_view"], auth = data["credentials"])
    assert response.status_code == HTTP_STATUS.BAD_REQUEST
    # No "Content-Type" set
    assert response.text == ticker_must_be_between_3_and_5_chars_but_has(ticker_too_short)    


def test_reserve_ticker_too_long(data):
    ticker_too_long = "QA_Ticker"
    ticker_url = furl(data["RESERVE_TICKER_ENDPOINT"]).add(path=ticker_too_long).url

    response = requests.post(ticker_url, json = data["pool_json_by_view"], auth = data["credentials"])
    assert response.status_code == HTTP_STATUS.BAD_REQUEST
    # No "Content-Type" set
    assert response.text == ticker_must_be_between_3_and_5_chars_but_has(ticker_too_long)


def test_reserve_ticker(data):
    valid_ticker = "QAT"
    ticker_url = furl(data["RESERVE_TICKER_ENDPOINT"]).add(path=valid_ticker).url

    response = requests.post(ticker_url, json = data["pool_json_by_view"], auth = data["credentials"])
    _validate_status_and_content_type(response, HTTP_STATUS.OK)
    assert response.json() == {'name': 'QAT'}


def test_already_reserved_ticker(data):
    reserved_ticker = "QAT"
    ticker_url = furl(data["RESERVE_TICKER_ENDPOINT"]).add(path=reserved_ticker).url

    response = requests.post(ticker_url, json = data["pool_json_by_hash"], auth = data["credentials"])
    assert response.status_code == HTTP_STATUS.BAD_REQUEST
    # No "Content-Type" set
    assert response.text == ticker_already_reserved(reserved_ticker)


def test_pool_rejection_errors(data):
    errors_url = furl(data["ERRORS_ENDPOINT"]).add(path=data["pool_with_errors"]).url

    response = requests.get(errors_url)
    _validate_status_and_content_type(response, HTTP_STATUS.OK)

    errors = response.json()
    assert len(errors) > 0

    for e in errors:
        assert e["poolId"] == data["pool_with_errors"]
        assert len(e["cause"]) > 0


def test_pool_rejection_errors_with_past_time_filter(data):
    errors_url = furl(data["ERRORS_ENDPOINT"]).add(path=data["pool_with_errors"]).add(args={'fromDate':'13.10.2020'}).url

    response = requests.get(errors_url)
    _validate_status_and_content_type(response, HTTP_STATUS.OK)

    errors = response.json()
    assert len(errors) > 0

    for e in errors:
        assert e["poolId"] == data["pool_with_errors"]
        assert len(e["cause"]) > 0


def test_pool_rejection_errors_with_future_time_filter(data):
    errors_url = furl(data["ERRORS_ENDPOINT"]).add(path=data["pool_with_errors"]).add(args={'fromDate':'09.09.2090'}).url

    response = requests.get(errors_url)
    _validate_status_and_content_type(response, HTTP_STATUS.OK)

    errors = response.json()
    assert len(errors) == 0


def test_pool_unregistrations(data):
    response = requests.get(data["RETIRED_ENDPOINT"])
    _validate_status_and_content_type(response, HTTP_STATUS.OK)

    unregistered_pools = response.json()
    assert len(unregistered_pools) > 0
    assert all(item in unregistered_pools for item in data["unregistered_pools_examples"]) == True


def test_fetch_policies(data, db_session):
    response = requests.post(data["POLICIES_ENDPOINT"], json = data["smash_mainnet_url"], auth = data["credentials"])
    _validate_status_and_content_type(response, HTTP_STATUS.OK)

    fetched_data = response.json()
    assert len(fetched_data["uniqueTickers"]) >= 0 # Reserved ticker functionality has never been used and it's still under discussion how to use it.
    assert fetched_data["smashURL"] == data["smash_mainnet_url"]
    assert fetched_data["healthStatus"] == {"status":"OK","version":"1.6.1"}
    assert len(fetched_data["delistedPools"]) >= 18

    delisted_pools_from_rest = set(pool["poolId"] for pool in fetched_data["delistedPools"])
    delisted_pools_from_db = get_delisted_pools_from_db(db_session)

    assert delisted_pools_from_rest == delisted_pools_from_db


def test_fetch_policies_invalid_smash_url(data):
    response = requests.post(data["POLICIES_ENDPOINT"], json = data["smash_invalid_url"], auth = data["credentials"])
    assert response.status_code == HTTP_STATUS.INTERNAL_SERVER_ERROR
    assert response.text == 'Something went wrong'
