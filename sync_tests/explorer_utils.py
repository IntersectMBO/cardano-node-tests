import time
import requests

MAINNET_EXPLORER_URL = "https://explorer.cardano.org/graphql"
STAGING_EXPLORER_URL = "https://explorer.staging.cardano.org/graphql"
TESTNET_EXPLORER_URL = "https://explorer.cardano-testnet.iohkdev.io/graphql"
SHELLEY_QA_EXPLORER_URL = "https://explorer.shelley-qa.dev.cardano.org/graphql"
PREPROD_EXPLORER_URL = None
PREVIEW_EXPLORER_URL = None


def get_epoch_start_datetime_from_explorer(env, epoch_no):
    global res, url
    headers = {'Content-type': 'application/json'}
    payload = '{"query":"query searchForEpochByNumber($number: Int!) {\\n  epochs(where: {number: ' \
              '{_eq: $number}}) {\\n    ...EpochOverview\\n  }\\n}\\n\\nfragment EpochOverview on ' \
              'Epoch {\\n  blocks(limit: 1) {\\n    protocolVersion\\n  }\\n  blocksCount\\n  ' \
              'lastBlockTime\\n  number\\n  startedAt\\n  output\\n  transactionsCount\\n}\\n",' \
              '"variables":{"number":' + str(epoch_no) + '}} '

    if env == "mainnet":
        url = MAINNET_EXPLORER_URL
    elif env == "staging":
        url = STAGING_EXPLORER_URL
    elif env == "testnet":
        url = TESTNET_EXPLORER_URL
    elif env == "shelley-qa":
        url = SHELLEY_QA_EXPLORER_URL
    elif env == "preprod":
        url = PREPROD_EXPLORER_URL
    elif env == "preview":
        url = PREVIEW_EXPLORER_URL
    else:
        print(f"!!! ERROR: the provided 'env' is not supported. Please use one of: shelley-qa, "
              f"testnet, staging, mainnet, preview, preprod")
        exit(1)
    if url is not None:
        res = requests.post(url, data=payload, headers=headers)
        status_code = res.status_code
        if status_code == 200:
            count = 0
            while "data" in res.json() and res.json()['data'] is None:
                print(f"response {count}: {res.json()}")
                time.sleep(30)
                count += 1
                if count > 10:
                    print(
                        f"!!! ERROR: Not able to get start time for epoch {epoch_no} on {env} after 10 tries")
                    return None
            return res.json()['data']['epochs'][0]['startedAt']
        else:
            print(f"status_code: {status_code}")
            print(f"response: {res.text}")
            print(
                f"!!! ERROR: status_code =! 200 when getting start time for epoch {epoch_no} on {env}")
            print(f"     - The Explorer might be down - {url}")
            return None
    else:
        return None


# def get_tx_count_per_epoch_from_explorer(env, epoch_no):
#     # we are collecting/displaying the tx_count per epoch only for Mainnet (and for that we are using Blockfrost)
#     global res, url
#     headers = {'Content-type': 'application/json'}
#     payload = '{"query":"query searchForEpochByNumber($number: Int!) {\\n  epochs(where: {number: ' \
#               '{_eq: $number}}) {\\n    ...EpochOverview\\n  }\\n}\\n\\nfragment EpochOverview on ' \
#               'Epoch {\\n  blocks(limit: 1) {\\n    protocolVersion\\n  }\\n  blocksCount\\n  ' \
#               'lastBlockTime\\n  number\\n  startedAt\\n  output\\n  transactionsCount\\n}\\n",' \
#               '"variables":{"number":' + str(epoch_no) + '}} '
#
#     if env == "mainnet":
#         url = MAINNET_EXPLORER_URL
#     elif env == "staging":
#         url = STAGING_EXPLORER_URL
#     elif env == "testnet":
#         url = TESTNET_EXPLORER_URL
#     elif env == "shelley-qa":
#         url = SHELLEY_QA_EXPLORER_URL
#     # elif env == "preprod":
#     #     url = PREPROD_EXPLORER_URL
#     # elif env == "preview":
#     #     url = PREVIEW_EXPLORER_URL
#     else:
#         print(f"!!! ERROR: the provided 'env' is not supported. Please use one of: shelley-qa, "
#               f"testnet, staging, mainnet, preprod, preview")
#         exit(1)
#
#     res = requests.post(url, data=payload, headers=headers)
#     status_code = res.status_code
#     if status_code == 200:
#         return res.json()['data']['epochs'][0]['transactionsCount']
#     else:
#         print(f"status_code: {status_code}")
#         print(f"response: {res.text}")
#         print(f"!!! ERROR: status_code =! 200 when getting txs_count for epoch {epoch_no} on {env}")
#         exit(1)


# def get_current_epoch_no_from_explorer(env):
#     # we are collecting/displaying the tx_count per epoch only for Mainnet (and for that we are using Blockfrost)
#     global res, url
#     headers = {'Content-type': 'application/json', 'Referer': 'https://explorer.cardano.org/en'}
#     payload = '{"query":"query cardanoDynamic {\\n  cardano {\\n    tip {\\n      number\\n      ' \
#               'slotInEpoch\\n      slotNo\\n      forgedAt\\n      protocolVersion\\n    }\\n    ' \
#               'currentEpoch {\\n      number\\n    }\\n  }\\n}\\n","variables":{}} '
#
#     if env == "mainnet":
#         url = MAINNET_EXPLORER_URL
#     elif env == "staging":
#         url = STAGING_EXPLORER_URL
#     elif env == "testnet":
#         url = TESTNET_EXPLORER_URL
#     elif env == "shelley-qa":
#         url = SHELLEY_QA_EXPLORER_URL
#     elif env == "preprod":
#         url = PREPROD_EXPLORER_URL
#     elif env == "preview":
#         url = PREVIEW_EXPLORER_URL
#     else:
#         print(f"!!! ERROR: the provided 'env' is not supported. Please use one of: shelley-qa, "
#               f"testnet, staging, mainnet, preprod, preview")
#         exit(1)
#
#     res = requests.post(url, headers=headers, data=payload)
#     status_code = res.status_code
#
#     if status_code == 200:
#         count = 0
#         while "data" in res.json() and res.json()['data'] is None:
#             print(f"response {count}: {res.json()}")
#             time.sleep(30)
#             count += 1
#             if count > 10:
#                 print("!!! ERROR: Not able to get the epochNo after 10 tries")
#                 exit(1)
#         return res.json()['data']['cardano']['currentEpoch']['number']
#     else:
#         print(f"status_code: {status_code}")
#         print(f"response: {res.text}")
#         print(
#             f"!!! ERROR: status_code =! 200 when getting the current epoch_no on mainnet")
#         print(f"     - The Explorer might be down - {url}")
#         exit(1)
