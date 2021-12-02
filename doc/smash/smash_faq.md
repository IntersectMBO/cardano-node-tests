# SMASH FAQ

Q: I can see that one pool has many reserved tickers. Is this a valid behavior ?

```sql
testnet_v12_6=# select * from reserved_pool_ticker where pool_hash='\x1c443cd9c14c85e6b541be0c2bd98c9f11cd25185a15636c44c4cd3f';
 id | name |                         pool_hash
----+------+------------------------------------------------------------
  4 | QA_3 | \x1c443cd9c14c85e6b541be0c2bd98c9f11cd25185a15636c44c4cd3f
  5 | QA_4 | \x1c443cd9c14c85e6b541be0c2bd98c9f11cd25185a15636c44c4cd3f
(2 rows)
```

A: Yes, this should be allowed. Reserved ticker functionality has never been used and it's still under discussion how to use it.

</br>
</br>

Q: I can delist pools that do not exist. Is this a valid behavior ?


0) Query some fake pool:

```sh
curl -X GET -v http://localhost:3101/api/v1/metadata/8517fa7042cb9494818861c53c87780b4975c0bd402e3ed85168aa66/4b2221a0ac0b0197308323080ba97e3e453f8625393d30f96eebe0fca4cb7335 | jq .
< NOTHING >
```

1) Delist it:

```
curl --verbose -u username:password --header "Content-Type: application/json" --request PATCH --data '{"poolId":"8517fa7042cb9494818861c53c87780b4975c0bd402e3ed85168aa66"}' http://localhost:3101/api/v1/delist

{"poolId":"8517fa7042cb9494818861c53c87780b4975c0bd402e3ed85168aa66"}
```

2) Check DB: last record with that pool_id added:

```sql
testnet_v12_6=# select * from delisted_pool;
 id |                          hash_raw
----+------------------------------------------------------------
  1 | \xa5a3ce765f5162548181a44d1ff8c8f8c50018cca59acc0b70a85a41
  2 | \x3a57885c1e896a939c0b71c8e070eaf742fbcdb214f62cede8b79b10
  5 | \x8517fa7042cb9494818861c53c87780b4975c0bd402e3ed85168aa66
```


A: Yes, this is a valid behavior, for example someone could start syncing and delist the pool either manually or using the fetch policies endpoint before they appear in the Blockchain. Keeping them separate means we don't impose any order.

</br>
</br>

Q: What happened to `testing-flag`:

```sh
flag testing-mode
  description: A flag for allowing operations that promote easy testing.
  default:     True
```

A: The flag was removed completely. `testing-mode` was supposed to enable manual insert endpoints like the POST metadata endpoint.
One can directly use the db to insert entries for testing so we are not missing any functionality by removing it.

</br>
</br>


Q: Can the count of results returned by **SMASH** retire endpoint differ from results count in **pool_retire** table ?

```sh
curl --header "Content-Type: application/json" http://localhost:3100/api/v1/retired | jq . > retired_pools.json
jq length retired_pools.json
306
```
VS

```sql
testnet_v12_6=# select count (*) from pool_retire;
count
-------
  346
(1 row)
```

A: Yes, if the pools re-registers. There has to be an entry in **pool_update** which is after the **announced_tx_id**
