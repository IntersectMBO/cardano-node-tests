# Testing MIR certificate submission with a local cluster

Some node network operations require access to genesis keys, and this requires proper access to the
machines where those nodes are running. To run these tests more quickly and more safely (in the worst case, the test can only break your local cluster, which you can easily respin), `local-cluster` is exactly what you need.

Below is an example of running a manual test for checking the MIR certificate transfer to a stake address that was registered and then deregistered before the MIR certificate transaction was submitted. This is a practical example of interacting with `local-cluster` - `cardano-node` and `db-sync`, which also shows how easily you can use `local-cluster` for examining Cardano features.

This assumes that you have a running local cluster.
Instructions on how to start a local cluster can be found here:
<https://input-output-hk.github.io/cardano-node-tests/how-tos/300_running_local_cluster.html>

## Creating key pairs

### Payment key pair

```sh
cardano-cli address key-gen \
--verification-key-file payment.vkey \
--signing-key-file payment.skey
```

### Stake key pair

```sh
cardano-cli stake-address key-gen \
--verification-key-file stake.vkey \
--signing-key-file stake.skey
```

### Payment address

```sh
cardano-cli address build \
--payment-verification-key-file payment.vkey \
--stake-verification-key-file stake.vkey \
--out-file payment.addr \
--testnet-magic 42
```

### Stake address

```sh
cardano-cli stake-address build \
--stake-verification-key-file stake.vkey \
--out-file stake.addr \
--testnet-magic 42
```

```sh
cat payment.addr
addr_test1qz57qp78v2udppvyxxxhsl959m8dc32axlplf2sxexz79kp87k8wm2uh9g2js0y942lkvu9lgu3eg93lqv5mn9jm66wsv55af7
```

```sh
cat stake.addr
stake_test1uqnltrhd4wtj59fg8jz640mxwzl5wgu5zclsx2dejedad8gmxw2ns
```


## Transferring funds from the 'faucet' address to our own Shelley address `payment.addr`

First query an address that holds funds:

```sh
cardano-cli query utxo --address $(cat /home/artur/Projects/cardano-node/state-cluster0/byron/address-000-converted) --testnet-magic 42
                           TxHash                                 TxIx        Amount
--------------------------------------------------------------------------------------
3409ede3ea6665b175c5a56e41187f9e62b4f02901b1437a935b90ad60042249     0        35996998496920237 lovelace + TxOutDatumHashNone
```

We will assume an arbitrary fee = 1 ADA
and send 2 mln ADA from the 'faucet' address (address-000-converted) to payment.addr:

```sh
expr 35996998496920237 - 2000000000000 - 1000000
35994998495920237
```


### Build tx

```sh
cardano-cli transaction build-raw \
--tx-in 3409ede3ea6665b175c5a56e41187f9e62b4f02901b1437a935b90ad60042249#0 \
--tx-out $(cat ~/Projects/payment.addr)+2000000000000 \
--tx-out $(cat /home/artur/Projects/cardano-node/state-cluster0/byron/address-000-converted)+35994998495920237 \
--fee 1000000 \
--out-file tx.raw
```

### Sign tx

```sh
cardano-cli transaction sign \
--tx-body-file tx.raw \
--signing-key-file /home/artur/Projects/cardano-node/state-cluster0/byron/payment-keys.000-converted.skey \
--testnet-magic 42 \
--out-file tx.signed
```

### Submit tx

```sh
cardano-cli transaction submit --tx-file tx.signed  --testnet-magic 42
Transaction successfully submitted.
```


### Check funds on the payment.addr

```sh
cardano-cli query utxo --address $(cat /home/artur/Projects/payment.addr) --testnet-magic 42
                           TxHash                                 TxIx        Amount
--------------------------------------------------------------------------------------
ece90464d725625a1d7d5f484b24199f067d6bddb3df9b05a67c6cc8dba6944e     0        2000000000000 lovelace + TxOutDatumHashNone
```

## Creating a stake registration certificate

```sh
cardano-cli stake-address registration-certificate \
--stake-verification-key-file stake.vkey \
--out-file stake.cert
```

There is a deposit required for `stake address`. Let's check its value:

```sh
cardano-cli query protocol-parameters --testnet-magic 42 | grep Deposit
    "stakePoolDeposit": 500000000,
    "stakeAddressDeposit": 400000,
```

`genesis.json` with value for deposits can be found: cardano-node/state-cluster0/shelley/genesis.json


Query the UTXO of the address that pays for the transaction and deposit:

```sh
cardano-cli query utxo --address $(cat /home/artur/Projects/payment.addr) --testnet-magic 42
                           TxHash                                 TxIx        Amount
--------------------------------------------------------------------------------------
ece90464d725625a1d7d5f484b24199f067d6bddb3df9b05a67c6cc8dba6944e     0        2000000000000 lovelace + TxOutDatumHashNone
```

Calculate the change to send back to the payment address after including the deposit:

```sh
fee = 1 ADA = 1000000 lovelaces
Stake Address Deposit = 400000 lovelaces

expr 2000000000000 - 1000000 - 400000
1999994000000
```

### Submitting the certificate with a transaction

#### Build tx

```sh
cardano-cli transaction build-raw \
--tx-in ece90464d725625a1d7d5f484b24199f067d6bddb3df9b05a67c6cc8dba6944e#0 \
--tx-out $(cat payment.addr)+1999998600000 \
--fee 1000000 \
--out-file tx002.raw \
--certificate-file stake.cert
```

#### Sign it

```sh
cardano-cli transaction sign \
--tx-body-file tx002.raw \
--signing-key-file payment.skey \
--signing-key-file stake.skey \
--testnet-magic 42 \
--out-file tx002.signed
```

#### And submit it

```sh
cardano-cli transaction submit \
--tx-file tx002.signed \
--testnet-magic 42
```

### Our stake address has id=13 and is registered

See the table below:
The stake address is `stake_test1uqnltrhd4wtj59fg8jz640mxwzl5wgu5zclsx2dejedad8gmxw2ns`.

```sql
select * from stake_address;
```

| id | hash_raw                                                     | view                                                             | registered_tx_id | script_hash |
| -- | ------------------------------------------------------------ | ---------------------------------------------------------------- | ---------------- | ----------- |
| 5  | \xe0568028c22bdf100e83979696540db02ec6a4eb1f1c8e9d5b91b77487 | stake_test1uptgq2xz9003qr5rj7tfv4qdkqhvdf8truwga82mjxmhfpcxv3p46 | 5                |             |
| 1  | \xe0e50e564992e494d163c310af81d858168286e6acdff5bf15d887ed19 | stake_test1urjsu4jfjtjff5trcvg2lqwctqtg9phx4n0lt0c4mzr76xgfd5c7u | 5                |             |
| 8  | \xe0c4e0c1c243bcb0ff38e835d8e35f29c1f7d277587d602d713cf1933e | stake_test1urzwpswzgw7tplecaq6a3c6l98ql05nhtp7kqtt38ncex0stcmuyj | 5                |             |
| 2  | \xe054ddf4c30186b9154afe55b42ba175d903794ecd66b785c68bd2e7c1 | stake_test1up2dmaxrqxrtj922le2mg2apwhvsx72we4nt0pwx30fw0sg607vyz | 5                |             |
| 11 | \xe01c60761146bc394d9fb4e47f9ffbd3fd53256ca52b88dc040f6be406 | stake_test1uqwxqas3g67rjnvlknj8l8lm6074xftv554c3hqypa47gpstqfku4 | 5                |             |
| 3  | \xe07940665a5f25116ac467b1d0fdc514b86cad61786c59d1a52fcce9da | stake_test1upu5qej6tuj3z6kyv7caplw9zjuxettp0pk9n5d99lxwnksl99hux | 5                |             |
| 13 | \xe027f58eedab972a15283c85aabf6670bf472394163f0329b9965bd69d | stake_test1uqnltrhd4wtj59fg8jz640mxwzl5wgu5zclsx2dejedad8gmxw2ns | 9                |             |


```sql
select * from stake_registration;
```

| id | addr_id | cert_index | tx_id | epoch_no |
| -- | ------- | ---------- | ----- | -------- |
| 1  | 1       | 0          | 5     | 1        |
| 2  | 5       | 1          | 5     | 1        |
| 3  | 2       | 4          | 5     | 1        |
| 4  | 8       | 5          | 5     | 1        |
| 5  | 3       | 8          | 5     | 1        |
| 6  | 11      | 9          | 5     | 1        |
| 7  | 13      | 0          | 10    | 34       |


## Deregistering an 'empty' stake address

 This address has no rewards because its stake was not delegated.

```sh
cardano-cli query stake-address-info --address stake_test1uqnltrhd4wtj59fg8jz640mxwzl5wgu5zclsx2dejedad8gmxw2ns --testnet-magic 42
[
    {
        "address": "stake_test1uqnltrhd4wtj59fg8jz640mxwzl5wgu5zclsx2dejedad8gmxw2ns",
        "rewardAccountBalance": 0,
        "delegation": null
    }
]
```

### Creating a deregistration certificate

```sh
cardano-cli stake-address deregistration-certificate \
--stake-verification-key-file stake.vkey \
--out-file deregistration.cert
```

```sh
cardano-cli query utxo --address $(cat payment.addr) --testnet-magic 42
                           TxHash                                 TxIx        Amount
--------------------------------------------------------------------------------------
a4c141cfae907aa1c4b418f65f384a6d860d52786b412481bc63733acfab1541     0        1999998600000 lovelace + TxOutDatumHashNone
```

#### Adding +400000 from a key deposit that will be returned

```sh
expr 1999998600000 + 400000 - 1000000
1999998000000
```

#### Build tx

```sh
cardano-cli transaction build-raw \
--tx-in a4c141cfae907aa1c4b418f65f384a6d860d52786b412481bc63733acfab1541#0 \
--tx-out $(cat payment.addr)+1999998000000 \
--fee 1000000 \
--out-file tx-deregister-stake-addr.raw \
--certificate-file deregistration.cert
```

#### Sign tx

```sh
cardano-cli transaction sign \
--tx-body-file tx-deregister-stake-addr.raw \
--signing-key-file payment.skey \
--signing-key-file stake.skey \
--testnet-magic 42 \
--out-file tx-deregister-stake-addr.signed
```

#### Submit txDeregistration event was registered in `db-sync`

```sql
select * from stake_deregistration;
```

| id | addr_id | cert_index | tx_id | epoch_no | redeemer_id  |
| -- | ------- | ---------- | ----- | -------- | ------------ |
| 1  | 13      | 0          | 11    | 39       |              |


**RESERVES** and **TREASURY** table state before MIR cert submission:

```sql
select * from reserve;
```

| id       | addr_id | cert_index | amount | tx_id  |
| --       | ------- | ---------- | ------ | ------ |
| (0 rows) |         |            |        |        |

```sql
select * from treasury;
```

| id       | addr_id | cert_index | amount | tx_id  |
| --       | ------- | ---------- | ------ | ------ |
| (0 rows) |         |            |        |        |


## Generating MIR cert to send funds from reserves to unregistered stake address

```sh
cardano-cli governance create-mir-certificate \
--stake-address stake_test1uqnltrhd4wtj59fg8jz640mxwzl5wgu5zclsx2dejedad8gmxw2ns \
--reserves \
--reward 500000000000 \
--out-file mir_reserves_500K_to_unregistered_stake_addr.cert
```

```sh
cardano-cli query utxo --address $(cat payment.addr) --testnet-magic 42
                            TxHash                               | TxIx |    Amount
 -----------------------------------------------------------------------------------
 277cab33552f06331af9dbf0d05635464a769dab05335511df7c7d4d70f41b61  0      1999998000000 lovelace + TxOutDatumHashNone
```

```sh
expr 1999998000000 - 1000000
1999997000000
```


### Build tx

```sh
cardano-cli transaction build-raw \
--tx-in 277cab33552f06331af9dbf0d05635464a769dab05335511df7c7d4d70f41b61#0 \
--tx-out $(cat payment.addr)+1999997000000 \
--fee 1000000 \
--out-file tx-mir-cert.raw \
--certificate-file mir_reserves_500K_to_unregistered_stake_addr.cert
```

### Sign tx

```sh
cardano-cli transaction sign \
--tx-body-file tx-mir-cert.raw \
--signing-key-file payment.skey \
--signing-key-file /home/artur/Projects/cardano-node/state-cluster0/shelley/delegate-keys/delegate1.skey \
--signing-key-file /home/artur/Projects/cardano-node/state-cluster0/shelley/genesis-keys/genesis1.skey \
--testnet-magic 42 \
--out-file tx-mir-cert.signed
```

### Submit tx

```sh
cardano-cli transaction submit \
--tx-file tx-mir-cert.signed \
--testnet-magic 42
```


## State of tables after MIR cert tx submission

```sql
select * from reserve;
```

| id | addr_id | cert_index | amount       | tx_id  |
| -- | ------  | ---------- | ------------ | ------ |
| 1  | 13      | 0          | 500000000000 | 12     |

```sql
select * from treasury;
```

| id       | addr_id | cert_index | amount | tx_id  |
| --       | ------- | ---------- | ------ | ------ |
| (0 rows) |         |            |        |        |


## Logs

We can see that currently there are some issues:

### stdout.log

```sh
[db-sync-node:Info:784][2021-10-08 12:45:33.21 UTC] Starting epoch 58
[db-sync-node:Info:784] [2021-10-08 12:45:33.21 UTC] Handling 3 stakes for epoch 58 slot 58011, hash f8175c985699105a67afdc7dae17b325fd1cafd3794500e95722fafecc375ce5
[db-sync-node:Warning:784][0m [2021-10-08 12:45:33.21 UTC] validateEpochRewards: rewards spendable in epoch 58 expected total of 1065.150011 ADA but got 501065.150011 ADA
```

```sql
select * from epoch_reward_total_received order by id DESC LIMIT 5;
```

| id | earned_epoch | amount        |
| -- | ------------ | ------------- |
| 57 | 56           | 501065150011  |
| 56 | 55           | 1196962304    |
| 55 | 54           | 1197126868    |
| 54 | 53           | 1197291461    |
| 53 | 52           | 1197456077    |

```sql
select reward.earned_epoch, pool_hash.view as delegated_pool, reward.amount as lovelace
    from reward inner join stake_address on reward.addr_id = stake_address.id
    inner join pool_hash on reward.pool_id = pool_hash.id
    where stake_address.view = 'stake_test1uqnltrhd4wtj59fg8jz640mxwzl5wgu5zclsx2dejedad8gmxw2ns'
    order by earned_epoch asc ;
```

`(NOTHING)`
