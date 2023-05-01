# SMASH - tests and debugging examples


These examples come from the `testnet` network.

**SMASH** can be built with:

`cabal build cardano-smash-server`


Find and copy the executable to your $PATH:

`cp $(find . -name cardano-smash-server -executable -type f) ~/.local/bin`


Prepare an `admins.txt` file which should have this format:

```text
username, password
```

and start **SMASH**:

```sh
PGPASSFILE=config/pgpass-mainnet cardano-smash-server \
     --config config/mainnet-config.yaml \
     --port 3100 \
     --admins admins.txt
```


## Checking SMASH status

```sh
curl --verbose --header "Content-Type: application/json" --request GET http://localhost:3100/api/v1/status

{"status":"OK","version":"12.0.0"}
```


## Fetching of the metadata

```sh
curl --verbose --header "Content-Type: application/json" --request GET http://localhost:3100/api/v1/metadata/a5a3ce765f5162548181a44d1ff8c8f8c50018cca59acc0b70a85a41/d98a03b8aa962d80511d62566df2af415afd9bd03d53cbb0ad457a53d3491f74

{"name": "MKS Stake Pool", "ticker": "MKS", "homepage": "http://23.234.197.69", "description": "testnet stake pool"}
```


## Delisting pools

a) Delisting a pool for the first time:

```sh
curl --verbose -u username:password --header "Content-Type: application/json" --request PATCH --data '{"poolId":"a5a3ce765f5162548181a44d1ff8c8f8c50018cca59acc0b70a85a41"}' http://localhost:3100/api/v1/delist

{"poolId":"a5a3ce765f5162548181a44d1ff8c8f8c50018cca59acc0b70a85a41"}
```

b) Delisting an already delisted pool results in:

```sh
curl --verbose -u username:password --header "Content-Type: application/json" --request PATCH --data '{"poolId":"a5a3ce765f5162548181a44d1ff8c8f8c50018cca59acc0b70a85a41"}' http://localhost:3100/api/v1/delist

{"code":"DbInsertError","description":"Delisted pool already exists!"}
```

```sql
select * from delisted_pool;
```

| id |                          hash_raw
| -- | -----------------------------------------------------------
| 1 | \xa5a3ce765f5162548181a44d1ff8c8f8c50018cca59acc0b70a85a41


## Reserving a ticker

a) Insert a new ticker for a pool:

```sh
curl --verbose -u username:password --header "Content-Type: application/json" --request POST --data '{"poolId":"1c443cd9c14c85e6b541be0c2bd98c9f11be25185a15636c33c4cd8f"}' http://localhost:3100/api/v1/tickers/ART

{"name":"ART"}
```

b) Insert an already reserved ticker for a different pool:

```sh
curl --verbose -u username:password --header "Content-Type: application/json" --request POST --data '{"poolId":"1c443cd9c14c85e6b541be0c2bd98c9f11cd25185a15636c44c4cd3f"}' http://localhost:3100/api/v1/tickers/ART

{"code":"TickerAlreadyReserved","description":"Ticker name ART is already reserved"}
```

```text
[smash-server:Info:6] [2021-11-23 12:06:29.94 UTC] SMASH listening on port 3100
[smash-server:Warning:27] [2021-11-23 12:59:18.14 UTC] TickerAlreadyReserved (TickerName "ART")
```

```sql
select * from reserved_pool_ticker;
```

| id | name |                         pool_hash
| -- | ---- | -----------------------------------------------------------
|  1 | ART  | \x1c443cd9c14c85e6b541be0c2bd98c9f11be25185a15636c33c4cd8f


## Whitelisting

a) Query some pool:

```sh
curl -X GET -v http://localhost:3100/api/v1/metadata/81e84003f3d2f65315b479dc3cdbe4aa8c8595a3d76818e284b29f27/8e123d238a44a91bab56ebd631020bede5a381d8487a46d1f2ebbdc52e3b3eba | jq .

{
  "name": "OZZIE",
  "ticker": "OZZIE",
  "homepage": "https://ozzieoffsec.github.io/pool",
  "description": "We love Turtles"
}
```

b) Delist it:

```sh
curl --verbose -u username:password --header "Content-Type: application/json" --request PATCH --data '{"poolId":"81e84003f3d2f65315b479dc3cdbe4aa8c8595a3d76818e284b29f27"}' http://localhost:3100/api/v1/delist

{"poolId":"81e84003f3d2f65315b479dc3cdbe4aa8c8595a3d76818e284b29f27"}
```

```sql
select * from delisted_pool;
```

| id | hash_raw
| -- | -----------------------------------------------------------
| 1  | \x81e84003f3d2f65315b479dc3cdbe4aa8c8595a3d76818e284b29f27


b) Query pool after it was delisted ==> no results

```sh
curl -X GET -v http://localhost:3100/api/v1/metadata/81e84003f3d2f65315b479dc3cdbe4aa8c8595a3d76818e284b29f27/8e123d238a44a91bab56ebd631020bede5a381d8487a46d1f2ebbdc52e3b3eba | jq .

Pool 81e84003f3d2f65315b479dc3cdbe4aa8c8595a3d76818e284b29f27 is delisted

[smash-server:Warning:40] [2021-11-25 10:02:36.40 UTC] Pool 81e84003f3d2f65315b479dc3cdbe4aa8c8595a3d76818e284b29f27 is delisted
```

c) Whitelist pool:

```sh
curl -u username:password -X PATCH -v http://localhost:3100/api/v1/enlist -H 'content-type: application/json' -d '{"poolId": "81e84003f3d2f65315b479dc3cdbe4aa8c8595a3d76818e284b29f27"}'

{"poolId":"81e84003f3d2f65315b479dc3cdbe4aa8c8595a3d76818e284b29f27"}
```

```sql
select * from delisted_pool;
```

| id       | hash_raw                                                    |
| --       | ----------------------------------------------------------- |
| (0 rows) |                                                             |

d) Query pool again:

```sh
curl -X GET -v http://localhost:3100/api/v1/metadata/81e84003f3d2f65315b479dc3cdbe4aa8c8595a3d76818e284b29f27/8e123d238a44a91bab56ebd631020bede5a381d8487a46d1f2ebbdc52e3b3eba | jq .

{
  "name": "OZZIE",
  "ticker": "OZZIE",
  "homepage": "https://ozzieoffsec.github.io/pool",
  "description": "We love Turtles"
}
```


## Checking pool rejection errors

```sh
curl http://localhost:3100/api/v1/errors/be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853?fromDate=13.10.2020
```

```json
[
   {
      "utcTime":"1635334386.697757s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":2,
      "cause":"URL parse error from for pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla resulted in : InvalidUrlException \"pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla\" \"Invalid URL\"",
      "poolHash":"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395",
      "time":"27.10.2021. 11:33:06"
   },
   {
      "utcTime":"1635334094.642995s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":0,
      "cause":"HTTP Response from for https://LLCJ.com resulted in : 509",
      "poolHash":"4001829c25b4af556d1a473dec4874a621899cf6a84c60156ec2411727f1a169",
      "time":"27.10.2021. 11:28:14"
   }
]
```


## Pool unregistrations

You can check what pools have unregistered with:

```sh
curl --header "Content-Type: application/json" http://localhost:3100/api/v1/retired | jq .
```

```json
[
  {
    "poolId": "002c501d063cf552144e58ea9a85c8d156b3d7c7a498e52a50cf546c"
  },
  {
    "poolId": "00cd5fd9cbf0b9535f804f59da4666859afa38e5ca7729a3172efe36"
  },
  {
    "poolId": "0192e6835f8613b1a47084d800ee3d2a0931a334e5e63dd80c447c15"
  },
  {
    "poolId": "01c2103a18e1dc55be3e83f7291266bba53a5b76c6c42f1969ee5193"
  },
  {
    "poolId": "other pools listed ..."
  }
]
```


## Fetch policies from another SMASH service

Fetch policies from another SMASH service:

* mainnet
   <https://smash.cardano-mainnet.iohk.io>
* testnet
  <https://smash.cardano-testnet.iohkdev.io>
* shelley-qa
  <https://smash.shelley-qa.dev.cardano.org>

```sh
curl -u username:password --verbose --header "Content-Type: application/json" --request POST --data '{"smashURL": "https://smash.cardano-mainnet.iohk.io"}' http://localhost:3100/api/v1/policies | jq .
```

```json
{
  "uniqueTickers": [],
  "smashURL": {
    "smashURL": "https://smash.cardano-mainnet.iohk.io"
  },
  "healthStatus": {
    "status": "OK",
    "version": "1.6.1"
  },
  "delistedPools": [
    {
      "poolId": "ce2e5bbae0caa514670d63cfdad3123a5d32cf7c37df87add5a0f75f"
    },
    {
      "poolId": "2b830258888a09e846b63474c642ad4e18aecd08dafb1f2a4d653e80"
    },
    {
      "poolId": "027a08f49ad5ece08e3a1575fb9cd8e8d7cf3b7815807a20b1a715f1"
    },
    {
      "poolId": "4eb1fac09251f8af19ad6b7e06b71cbad09dbe896b481e4670fe565d"
    },
    {
      "poolId": "bf44d3187cbdd8874dca1f714a6107beea642753228490bc02c8e038"
    },
    {
      "poolId": "00429f0a3e8c48d644a9b45babd09b86c367efe745a35b31f10e859f"
    },
    {
      "poolId": "8bc067247b8a85500d40d7bb78afd4de6a5fed2cfcc82c9b9c2fa8a2"
    },
    {
      "poolId": "e7e18f2050fa307fc9405f1d517760e894f8fbdf41a9b1b280571b38"
    },
    {
      "poolId": "27f4e3c309659f824026893b811dd6e70332881867cb2cba4974191c"
    },
    {
      "poolId": "c73186434c6fc6676bd67304d34518fc6fd7d5eaddaf78641b1e7dcf"
    },
    {
      "poolId": "2064da38531dad327135edd98003032cefa059c4c8c50c2b0440c63d"
    },
    {
      "poolId": "d9df218f8099261e019bdd304b9a40228070ce61272af835ea13d161"
    },
    {
      "poolId": "d7d56e1703630780176cf944a77b7829b4ba97888fa9a32468011985"
    },
    {
      "poolId": "82e5cb6e4b443c36b087e6218a5629291585d35083ce2cb625506e1f"
    },
    {
      "poolId": "0e76c44520b9d7f2e211eccd82de49350288368802c7aaa72a13c3fa"
    },
    {
      "poolId": "d471e981d54a7f60496f9239d2d706db7a71df8517025f478c112e3e"
    },
    {
      "poolId": "f537b3a5ac2ecdc854a535a15f7732632375a0bf2af17dccbe5b422d"
    },
    {
      "poolId": "033fa1cdc17193fa3d549e795591999621e749fd7ef48f7380468d14"
    }
  ]
}
```

and let's compare SMASH reply with database state:

```sh
cat smash_imported_policies.json | jq -r '.delistedPools | length'
18
```

VS

```sql
select * from delisted_pool;
```

| id  | hash_raw
| --- | ----------------------------------------------------------
| 382 | \xce2e5bbae0caa514670d63cfdad3123a5d32cf7c37df87add5a0f75f
| 383 | \x2b830258888a09e846b63474c642ad4e18aecd08dafb1f2a4d653e80
| 384 | \x027a08f49ad5ece08e3a1575fb9cd8e8d7cf3b7815807a20b1a715f1
| 385 | \x4eb1fac09251f8af19ad6b7e06b71cbad09dbe896b481e4670fe565d
| 386 | \xbf44d3187cbdd8874dca1f714a6107beea642753228490bc02c8e038
| 387 | \x00429f0a3e8c48d644a9b45babd09b86c367efe745a35b31f10e859f
| 388 | \x8bc067247b8a85500d40d7bb78afd4de6a5fed2cfcc82c9b9c2fa8a2
| 389 | \xe7e18f2050fa307fc9405f1d517760e894f8fbdf41a9b1b280571b38
| 390 | \x27f4e3c309659f824026893b811dd6e70332881867cb2cba4974191c
| 391 | \xc73186434c6fc6676bd67304d34518fc6fd7d5eaddaf78641b1e7dcf
| 392 | \x2064da38531dad327135edd98003032cefa059c4c8c50c2b0440c63d
| 393 | \xd9df218f8099261e019bdd304b9a40228070ce61272af835ea13d161
| 394 | \xd7d56e1703630780176cf944a77b7829b4ba97888fa9a32468011985
| 395 | \x82e5cb6e4b443c36b087e6218a5629291585d35083ce2cb625506e1f
| 396 | \x0e76c44520b9d7f2e211eccd82de49350288368802c7aaa72a13c3fa
| 397 | \xd471e981d54a7f60496f9239d2d706db7a71df8517025f478c112e3e
| 398 | \xf537b3a5ac2ecdc854a535a15f7732632375a0bf2af17dccbe5b422d
| 399 | \x033fa1cdc17193fa3d549e795591999621e749fd7ef48f7380468d14


## An example of debugging an issue with a pool

Here is an example of unusual behavior of a pool on `testnet` that could not be seen in any tool that tracks pools and at the same time, it could not be found in retired pools.

We have a pool `pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla` that can't be found in [Daedalus](https://buildkite.com/input-output-hk/daedalus/builds?branch=release%2F4.5.2) (version for `testnet`) nor on <https://pooltool.io/> (which is a web tool for listing pool details on both `mainnet` and `testnet`) and it is also not listed in the **pool_retire** table:


```sql
select * from pool_hash where view='pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla';
```

 id  | hash_raw                                                   | view
---- | ---------------------------------------------------------  | ----------------------------------------------------------
 103 | \xbe329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853 | pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla


**pool_retire**:

```sql
select * from pool_retire where hash_id=103;
```

| id       | hash_id | cert_index | announced_tx_id | retiring_epoch |
| ---      | ------- | ---------- | --------------- | -------------- |
| (0 rows) |         |            |                 |                |


Let's check the last update for this pool

**pool_update**:


 id  | hash_id | cert_index | vrf_key_hash                                                       | pledge     | reward_addr                                                  | active_epoch_no | meta_id | margin | fixed_cost | registered_tx_id
---- | ------- | ---------- | ------------------------------------------------------------------ | ---------- | ------------------------------------------------------------ | --------------- | ------- | ------ | ---------- | -----------------
 103 | 103     | 0          | \xfdbcad682e1462b1a107fb204316e73b4791aba0691410bdb5a6c219a0a16fe6 | 100000000  | \xe0d6de2014f77443a986d2ae9144fbcdcf08da0ddea1c0ce0f7e311d68 | 85              | 101     | 0.04   | 340000000  | 25037
 118 | 103     | 0          | \xfdbcad682e1462b1a107fb204316e73b4791aba0691410bdb5a6c219a0a16fe6 | 250000000  | \xe0d6de2014f77443a986d2ae9144fbcdcf08da0ddea1c0ce0f7e311d68 | 87              | 116     | 0.01   | 340000000  | 34587
 292 | 103     | 0          | \xfdbcad682e1462b1a107fb204316e73b4791aba0691410bdb5a6c219a0a16fe6 | 250000000  | \xe0d6de2014f77443a986d2ae9144fbcdcf08da0ddea1c0ce0f7e311d68 | 90              | 290     | 0.04   | 430000000  | 55996
 303 | 103     | 0          | \xfdbcad682e1462b1a107fb204316e73b4791aba0691410bdb5a6c219a0a16fe6 | 3495862056 | \xe0d6de2014f77443a986d2ae9144fbcdcf08da0ddea1c0ce0f7e311d68 | 91              | 301     | 0.0095 | 340000000  | 56543

(4 rows)


and metadata associated with it

**pool_metadata_ref**:

```sql
select * from pool_metadata_ref where pool_id=103;
```

 id  | pool_id | url                    | hash                                                               | registered_tx_id
---- | ------- | --------------------   | ------------------------------------------------------------------ | -----------------
 301 | 103     | <https://git.io/JTUAD> | \xf2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395 | 56543
 101 | 103     | <https://git.io/JU8gA> | \x314699218763d2d0c1c2cc75d4405de67709a888f01a4ca4d2b0f290e285c6e1 | 25037
 290 | 103     | <https://git.io/JUNOy> | \x94be57c392f721154c96bafbf9ebd80fe369b35ec8f177b292329ee21db25cbf | 55996
 116 | 103     | <https://LLCJ.com>     | \x4001829c25b4af556d1a473dec4874a621899cf6a84c60156ec2411727f1a169 | 34587


So our pool is not visible in tools like wallet and it is also not present in **pool_retire** table.
Private pools are not listed by those tools however last records from **pool_update** table listed above show that this is not a private pool because it has a `margin < 100%`.


Let's check the pool relay:

```sql
select * from pool_relay where update_id=303;
```

| id   | update_id | ipv4         | ipv6 | dns_name | dns_srv_name | port
| ---- | --------- | ------------ | ---- | -------- | ------------ | -----
| 1426 | 303       | 72.184.59.65 |      |          |              | 3001

and try to ping it:

```sh
ping 72.184.59.65
PING 72.184.59.65 (72.184.59.65) 56(84) bytes of data.
64 bytes from 72.184.59.65: icmp_seq=1 ttl=47 time=177 ms
64 bytes from 72.184.59.65: icmp_seq=2 ttl=47 time=184 ms
```

We can see that it is up.


Now let's check the ledger state and see if the record for our pool is still present there:

```sh
cardano-cli query stake-distribution --testnet-magic 1097911063 | grep pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla
pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla   1.611e-7
```

It is.

Querying **SMASH** using `pool_hash=be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853` and metadata hash from `pool_metadata_ref` for last `id=301` which is: `f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395` returns:

```sh
curl --verbose --header "Content-Type: application/json" --request GET http://localhost:3100/api/v1/metadata/be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853/f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395

{"code":"DbLookupPoolMetadataHash","description":"The metadata with hash PoolMetadataHash \"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395\" for pool PoolId \"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853\" is missing from the DB."}

[smash-server:Warning:124] [2021-11-24 12:48:43.03 UTC] DbLookupPoolMetadataHash (PoolId "be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853") (PoolMetadataHash "f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395")
```


Let's check errors for that pool:

```sh
curl http://localhost:3100/api/v1/errors/pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla
```

```json
[
   {
      "utcTime":"1637316510.524683s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":15,
      "cause":"URL parse error from for pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla resulted in : InvalidUrlException \"pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla\" \"Invalid URL\"",
      "poolHash":"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395",
      "time":"19.11.2021. 10:08:30"
   },
   {
      "utcTime":"1637316510.524683s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":13,
      "cause":"URL parse error from for pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla resulted in : InvalidUrlException \"pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla\" \"Invalid URL\"",
      "poolHash":"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395",
      "time":"19.11.2021. 10:08:30"
   },
   {
      "utcTime":"1637316510.524683s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":14,
      "cause":"URL parse error from for pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla resulted in : InvalidUrlException \"pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla\" \"Invalid URL\"",
      "poolHash":"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395",
      "time":"19.11.2021. 10:08:30"
   },
   {
      "utcTime":"1637316510.524683s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":0,
      "cause":"Hash mismatch from when fetching metadata from https://git.io/JTUAD. Expected f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395 but got 75848f572990cbc72a9efd62ed5aa0178e16d5164c6dcfbb040dcec47f13d8f4.",
      "poolHash":"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395",
      "time":"19.11.2021. 10:08:30"
   },
   {
      "utcTime":"1637316510.524683s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":1,
      "cause":"URL parse error from for pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla resulted in : InvalidUrlException \"pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla\" \"Invalid URL\"",
      "poolHash":"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395",
      "time":"19.11.2021. 10:08:30"
   },
   {
      "utcTime":"1637316510.524683s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":2,
      "cause":"URL parse error from for pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla resulted in : InvalidUrlException \"pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla\" \"Invalid URL\"",
      "poolHash":"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395",
      "time":"19.11.2021. 10:08:30"
   },
   {
      "utcTime":"1637316510.524683s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":3,
      "cause":"URL parse error from for pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla resulted in : InvalidUrlException \"pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla\" \"Invalid URL\"",
      "poolHash":"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395",
      "time":"19.11.2021. 10:08:30"
   },
   {
      "utcTime":"1637316510.524683s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":4,
      "cause":"URL parse error from for pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla resulted in : InvalidUrlException \"pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla\" \"Invalid URL\"",
      "poolHash":"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395",
      "time":"19.11.2021. 10:08:30"
   },
   {
      "utcTime":"1637316510.524683s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":5,
      "cause":"URL parse error from for pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla resulted in : InvalidUrlException \"pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla\" \"Invalid URL\"",
      "poolHash":"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395",
      "time":"19.11.2021. 10:08:30"
   },
   {
      "utcTime":"1637316510.524683s",
      "poolId":"be329bbf0ee0f53d19f3b2808611779a49c7df43f330a8035eb9f853",
      "retryCount":6,
      "cause":"URL parse error from for pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla resulted in : InvalidUrlException \"pool1hcefh0cwur6n6x0nk2qgvythnfyu0h6r7vc2sq67h8u9x8z2cla\" \"Invalid URL\"",
      "poolHash":"f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395",
      "time":"19.11.2021. 10:08:30"
   }
]
```

For hash `f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395`, the URL from **pool_metadata_ref** is:


 id  | pool_id | url                    | hash                                                               | registered_tx_id
---- | ------- | --------------------   | ------------------------------------------------------------------ | -----------------
 301 | 103     | <https://git.io/JTUAD> | \xf2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395 | 56543


`https://git.io/JTUAD` opens correctly a GH page with the following JSON:

```json
{
"name": "Lpool",
"description": "L pool is cool",
"ticker": "LPO",
"homepage": "https://git.io/JUNOy"
}
```

It seems that for this pool, 4 metadata references have been registered (from **pool_metadata_ref** table), but 3 of them failed and only one succeeded.

The **pool_offline_data** table shows different metadata - the one that matches the record with `id=290` in **pool_metadata_ref**
(homepage <https://llcj.com/> differs from JSON presented above <https://git.io/JUNOy>)


```sql
select id, pool_id, json from pool_offline_data where pool_id=103;
```

 id  | pool_id | json
---- | ------- | ----------------------------------------------------------------------------------------------------
 138 | 103     | `{"name": "Lpool", "ticker": "LPO", "homepage": "https://LLCJ.com", "description": "L pool is cool"}`


but this is not the last entry in **pool_metadata_ref** which has `id=301` and  `homepage=https://git.io/JTUAD`.

The reason for this problem is mentioned in one of the error messages returned by **SMASH**:

```text
"cause":"Hash mismatch from when fetching metadata from https://git.io/JTUAD. Expected f2b553839dee1ad1d16127179d4378a0c06a1fddce83409ad4b6f10b65bad395 but got 75848f572990cbc72a9efd62ed5aa0178e16d5164c6dcfbb040dcec47f13d8f4."
```

If we download the metadata file and check its hash:

```sh
wget https://git.io/JTUAD
cardano-cli stake-pool metadata-hash --pool-metadata-file JTUAD
75848f572990cbc72a9efd62ed5aa0178e16d5164c6dcfbb040dcec47f13d8f4
```

we can see in fact that its hash is different from what was expected.

It looks like someone decided to update the last successful metadata for the pool (`id=290` in **pool_metadata_ref**) but declared a wrong hash for a new metadata file and now the pool can't be correctly listed by **SMASH**.
