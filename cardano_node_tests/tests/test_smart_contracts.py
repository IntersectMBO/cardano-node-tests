"""Tests for smart contracts."""
import datetime
import distutils.spawn
import itertools
import logging
from pathlib import Path
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"

PLUTUS_DIR = DATA_DIR / "plutus"
ALWAYS_SUCCEEDS_PLUTUS = PLUTUS_DIR / "always-succeeds-spending.plutus"
ALWAYS_FAILS_PLUTUS = PLUTUS_DIR / "always-fails.plutus"
GUESSING_GAME_PLUTUS = PLUTUS_DIR / "custom-guess-42-datum-42.plutus"
MINTING_PLUTUS = PLUTUS_DIR / "anyone-can-mint.plutus"
TIME_RANGE_PLUTUS = PLUTUS_DIR / "time_range.plutus"
CONTEXT_EQUIVALENCE_PLUTUS = PLUTUS_DIR / "context-equivalence-test.plutus"
MINTING_CONTEXT_EQUIVALENCE_PLUTUS = PLUTUS_DIR / "minting-context-equivalence-test.plutus"

SIGNING_KEY_GOLDEN = DATA_DIR / "signing_key_golden"


class PlutusOp(NamedTuple):
    script_file: Path
    datum_file: Path
    redeemer_file: Path
    execution_units: Optional[Tuple[int, int]] = None


class Token(NamedTuple):
    coin: str
    amount: int


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    p = Path(tmp_path_factory.getbasetemp()).joinpath(helpers.get_id_for_mktemp(__file__)).resolve()
    p.mkdir(exist_ok=True, parents=True)
    return p


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


@pytest.fixture
def cluster_lock_always_suceeds(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Make sure just one txin plutus test run at a time.

    Plutus script always has the same address. When one script is used in multiple
    tests that are running in parallel, the blanaces etc. don't add up.
    """
    return cluster_manager.get(lock_resources=["always_suceeds_script"])


@pytest.fixture
def payment_addrs_lock_always_suceeds(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_always_suceeds: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment address while using the `cluster_lock_always_suceeds` fixture."""
    cluster = cluster_lock_always_suceeds
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            *[
                f"plutus_payment_lock_allsucceeds_ci{cluster_manager.cluster_instance_num}_{i}"
                for i in range(6)
            ],
            cluster_obj=cluster,
        )
        fixture_cache.value = addrs

    # fund source address
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        addrs[2],
        addrs[4],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=20_000_000_000,
    )

    return addrs


@pytest.fixture
def cluster_lock_guessing_game(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Make sure just one guessing game plutus test run at a time.

    Plutus script always has the same address. When one script is used in multiple
    tests that are running in parallel, the blanaces etc. don't add up.
    """
    return cluster_manager.get(lock_resources=["guessing_game_script"])


@pytest.fixture
def payment_addrs_lock_guessing_game(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_guessing_game: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment address while using the `cluster_lock_guessing_game` fixture."""
    cluster = cluster_lock_guessing_game
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            *[
                f"plutus_payment_lock_ggame_ci{cluster_manager.cluster_instance_num}_{i}"
                for i in range(4)
            ],
            cluster_obj=cluster,
        )
        fixture_cache.value = addrs

    # fund source address
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        addrs[2],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=20_000_000_000,
    )

    return addrs


@pytest.fixture
def cluster_lock_context_eq(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Make sure just one guessing game plutus test run at a time.

    Plutus script always has the same address. When one script is used in multiple
    tests that are running in parallel, the blanaces etc. don't add up.
    """
    return cluster_manager.get(lock_resources=["context_eq_script"])


@pytest.fixture
def pool_users_lock_context_eq(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_context_eq: clusterlib.ClusterLib,
) -> List[clusterlib.PoolUser]:
    """Create new pool users while using the `cluster_lock_context_eq` fixture."""
    cluster = cluster_lock_context_eq
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        created_users = clusterlib_utils.create_pool_users(
            cluster_obj=cluster,
            name_template="plutus_payment_lock_context_eq_ci"
            f"{cluster_manager.cluster_instance_num}",
            no_of_addr=4,
        )
        fixture_cache.value = created_users

    # fund source address
    clusterlib_utils.fund_from_faucet(
        created_users[0],
        created_users[2],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=20_000_000_000,
    )

    return created_users


@pytest.fixture
def cluster_lock_always_fails(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Make sure just one txin plutus test run at a time.

    Plutus script always has the same address. When one script is used in multiple
    tests that are running in parallel, the blanaces etc. don't add up.
    """
    return cluster_manager.get(lock_resources=["always_fails_script"])


@pytest.fixture
def payment_addrs_lock_always_fails(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_always_fails: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment address while using the `cluster_lock_always_fails` fixture."""
    cluster = cluster_lock_always_fails
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            *[
                f"plutus_payment_lock_allfails_ci{cluster_manager.cluster_instance_num}_{i}"
                for i in range(2)
            ],
            cluster_obj=cluster,
        )
        fixture_cache.value = addrs

    # fund source address
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=20_000_000_000,
    )

    return addrs


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment address."""
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            *[f"plutus_payment_ci{cluster_manager.cluster_instance_num}_{i}" for i in range(12)],
            cluster_obj=cluster,
        )
        fixture_cache.value = addrs

    # fund source address
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        addrs[2],
        addrs[4],
        addrs[6],
        addrs[8],
        addrs[10],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=10_000_000_000,
    )

    return addrs


def _fund_script(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    plutus_op: PlutusOp,
    amount: int,
    deposit_amount: int = 0,
    tokens: Optional[List[Token]] = None,  # tokens must already be in `payment_addr`
    tokens_collateral: Optional[List[Token]] = None,  # tokens must already be in `payment_addr`
    collateral_fraction_offset: float = 1.0,
) -> clusterlib.TxRawOutput:
    """Fund a plutus script and create the locked UTxO and collateral UTxO."""
    assert plutus_op.execution_units, "Execution units not provided"
    plutusrequiredtime, plutusrequiredspace = plutus_op.execution_units

    stokens = tokens or ()
    ctokens = tokens_collateral or ()

    script_address = cluster_obj.gen_script_addr(
        addr_name=temp_template, script_file=plutus_op.script_file
    )

    fee_redeem = int(plutusrequiredtime + plutusrequiredspace) + 10_000_000
    collateral_fraction = cluster_obj.get_protocol_params()["collateralPercentage"] / 100
    collateral_amount = int(fee_redeem * collateral_fraction * collateral_fraction_offset)

    script_init_balance = cluster_obj.get_address_balance(script_address)

    # create a Tx ouput with a datum hash at the script address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )
    datum_hash = cluster_obj.get_hash_script_data(script_data_file=plutus_op.datum_file)
    txouts = [
        clusterlib.TxOut(
            address=script_address,
            amount=amount + fee_redeem + deposit_amount,
            datum_hash=datum_hash,
        ),
        # for collateral
        clusterlib.TxOut(address=dst_addr.address, amount=collateral_amount),
    ]

    for token in stokens:
        txouts.append(
            clusterlib.TxOut(
                address=script_address,
                amount=token.amount,
                coin=token.coin,
                datum_hash=datum_hash,
            )
        )

    for token in ctokens:
        txouts.append(
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=token.amount,
                coin=token.coin,
            )
        )

    fee = cluster_obj.calculate_tx_fee(
        src_address=payment_addr.address,
        txouts=txouts,
        tx_name=f"{temp_template}_step1",
        tx_files=tx_files,
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
        witness_count_add=2,
    )
    tx_raw_output = cluster_obj.build_raw_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        txouts=txouts,
        tx_files=tx_files,
        fee=fee,
    )
    tx_signed = cluster_obj.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step1",
    )

    cluster_obj.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)

    script_balance = cluster_obj.get_address_balance(script_address)
    assert (
        script_balance == script_init_balance + amount + fee_redeem + deposit_amount
    ), f"Incorrect balance for script address `{script_address}`"

    for token in stokens:
        assert (
            cluster_obj.get_address_balance(script_address, coin=token.coin) == token.amount
        ), f"Incorrect token balance for script address `{script_address}`"

    for token in ctokens:
        assert (
            cluster_obj.get_address_balance(dst_addr.address, coin=token.coin) == token.amount
        ), f"Incorrect token balance for address `{dst_addr.address}`"

    dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    return tx_raw_output


def _spend_locked_txin(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    dst_addr: clusterlib.AddressRecord,
    script_utxos: List[clusterlib.UTXOData],
    collateral_utxos: List[clusterlib.UTXOData],
    plutus_op: PlutusOp,
    amount: int,
    deposit_amount: int = 0,
    tx_files: Optional[clusterlib.TxFiles] = None,
    invalid_hereafter: Optional[int] = None,
    invalid_before: Optional[int] = None,
    tokens: Optional[List[Token]] = None,
    expect_failure: bool = False,
    script_valid: bool = True,
    submit_tx: bool = True,
) -> Tuple[str, clusterlib.TxRawOutput]:
    """Spend the locked UTxO."""
    # pylint: disable=too-many-arguments
    assert plutus_op.execution_units, "Execution units not provided"
    plutusrequiredtime, plutusrequiredspace = plutus_op.execution_units

    tx_files = tx_files or clusterlib.TxFiles()
    spent_tokens = tokens or ()

    script_address = script_utxos[0].address
    fee_redeem = int(plutusrequiredtime + plutusrequiredspace) + 10_000_000

    script_init_balance = cluster_obj.get_address_balance(script_address)
    dst_init_balance = cluster_obj.get_address_balance(dst_addr.address)

    # spend the "locked" UTxO

    plutus_txins = [
        clusterlib.PlutusTxIn(
            txins=script_utxos,
            collaterals=collateral_utxos,
            script_file=plutus_op.script_file,
            execution_units=(plutusrequiredtime, plutusrequiredspace),
            datum_file=plutus_op.datum_file,
            redeemer_file=plutus_op.redeemer_file,
        )
    ]
    tx_files = tx_files._replace(
        signing_key_files=list({*tx_files.signing_key_files, dst_addr.skey_file}),
    )
    txouts = [
        clusterlib.TxOut(address=dst_addr.address, amount=amount),
    ]

    for token in spent_tokens:
        txouts.append(
            clusterlib.TxOut(address=dst_addr.address, amount=token.amount, coin=token.coin)
        )

    tx_raw_output = cluster_obj.build_raw_tx_bare(
        out_file=f"{temp_template}_step2_tx.body",
        txouts=txouts,
        tx_files=tx_files,
        fee=fee_redeem,
        plutus_txins=plutus_txins,
        invalid_hereafter=invalid_hereafter,
        invalid_before=invalid_before,
        script_valid=script_valid,
    )
    tx_signed = cluster_obj.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step2",
    )

    if not submit_tx:
        return "", tx_raw_output

    if not script_valid:
        cluster_obj.submit_tx(tx_file=tx_signed, txins=collateral_utxos)

        assert (
            cluster_obj.get_address_balance(dst_addr.address)
            == dst_init_balance - collateral_utxos[0].amount
        ), f"Collateral was NOT spent from `{dst_addr.address}`"

        assert (
            cluster_obj.get_address_balance(script_address) == script_init_balance
        ), f"Incorrect balance for script address `{script_address}`"

        return "", tx_raw_output

    if expect_failure:
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.submit_tx_bare(tx_file=tx_signed)
        err = str(excinfo.value)
        assert (
            cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance
        ), f"Collateral was spent from `{dst_addr.address}`"

        assert (
            cluster_obj.get_address_balance(script_address) == script_init_balance
        ), f"Incorrect balance for script address `{script_address}`"

        return err, tx_raw_output

    cluster_obj.submit_tx(tx_file=tx_signed, txins=[t.txins[0] for t in tx_raw_output.plutus_txins])

    assert (
        cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance + amount
    ), f"Incorrect balance for destination address `{dst_addr.address}`"

    assert (
        cluster_obj.get_address_balance(script_address)
        == script_init_balance - amount - fee_redeem - deposit_amount
    ), f"Incorrect balance for script address `{script_address}`"

    for token in spent_tokens:
        assert (
            cluster_obj.get_address_balance(script_address, coin=token.coin) == 0
        ), f"Incorrect token balance for script address `{script_address}`"

    dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    return "", tx_raw_output


def _build_fund_script(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    plutus_op: PlutusOp,
    tokens: Optional[List[Token]] = None,  # tokens must already be in `payment_addr`
    tokens_collateral: Optional[List[Token]] = None,  # tokens must already be in `payment_addr`
) -> clusterlib.TxRawOutput:
    """Fund a plutus script and create the locked UTxO and collateral UTxO.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    script_fund = 1000_000_000
    collateral_fund = 1500_000_000

    stokens = tokens or ()
    ctokens = tokens_collateral or ()

    script_address = cluster_obj.gen_script_addr(
        addr_name=temp_template, script_file=plutus_op.script_file
    )
    script_init_balance = cluster_obj.get_address_balance(script_address)

    # create a Tx ouput with a datum hash at the script address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )
    datum_hash = cluster_obj.get_hash_script_data(script_data_file=plutus_op.datum_file)
    txouts = [
        clusterlib.TxOut(address=script_address, amount=script_fund, datum_hash=datum_hash),
        # for collateral
        clusterlib.TxOut(address=dst_addr.address, amount=collateral_fund),
    ]

    for token in stokens:
        txouts.append(
            clusterlib.TxOut(
                address=script_address,
                amount=token.amount,
                coin=token.coin,
                datum_hash=datum_hash,
            )
        )

    for token in ctokens:
        txouts.append(
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=token.amount,
                coin=token.coin,
            )
        )

    tx_output = cluster_obj.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        tx_files=tx_files,
        txouts=txouts,
        fee_buffer=2_000_000,
    )
    tx_signed = cluster_obj.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step1",
    )
    cluster_obj.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    script_balance = cluster_obj.get_address_balance(script_address)
    assert (
        script_balance == script_init_balance + script_fund
    ), f"Incorrect balance for script address `{script_address}`"

    for token in stokens:
        assert (
            cluster_obj.get_address_balance(script_address, coin=token.coin) == token.amount
        ), f"Incorrect token balance for script address `{script_address}`"

    for token in ctokens:
        assert (
            cluster_obj.get_address_balance(dst_addr.address, coin=token.coin) == token.amount
        ), f"Incorrect token balance for address `{dst_addr.address}`"

    dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)

    return tx_output


def _build_spend_locked_txin(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    script_utxos: List[clusterlib.UTXOData],
    collateral_utxos: List[clusterlib.UTXOData],
    plutus_op: PlutusOp,
    amount: int,
    deposit_amount: int = 0,
    tx_files: Optional[clusterlib.TxFiles] = None,
    invalid_hereafter: Optional[int] = None,
    invalid_before: Optional[int] = None,
    tokens: Optional[List[Token]] = None,
    expect_failure: bool = False,
    script_valid: bool = True,
    submit_tx: bool = True,
) -> Tuple[str, Optional[clusterlib.TxRawOutput]]:
    """Spend the locked UTxO.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    # pylint: disable=too-many-arguments
    script_address = script_utxos[0].address
    tx_files = tx_files or clusterlib.TxFiles()
    spent_tokens = tokens or ()

    script_init_balance = cluster_obj.get_address_balance(script_address)
    dst_init_balance = cluster_obj.get_address_balance(dst_addr.address)

    # spend the "locked" UTxO

    plutus_txins = [
        clusterlib.PlutusTxIn(
            txins=script_utxos,
            collaterals=collateral_utxos,
            script_file=plutus_op.script_file,
            datum_file=plutus_op.datum_file,
            redeemer_file=plutus_op.redeemer_file,
        )
    ]
    tx_files = tx_files._replace(
        signing_key_files=list({*tx_files.signing_key_files, dst_addr.skey_file}),
    )
    txouts = [
        clusterlib.TxOut(address=dst_addr.address, amount=amount),
    ]

    for token in spent_tokens:
        txouts.append(
            clusterlib.TxOut(address=dst_addr.address, amount=token.amount, coin=token.coin)
        )

    if expect_failure:
        with pytest.raises(clusterlib.CLIError) as excinfo:
            tx_output = cluster_obj.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files,
                txouts=txouts,
                change_address=script_address,
                plutus_txins=plutus_txins,
            )
        return str(excinfo.value), None

    tx_output = cluster_obj.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step2",
        tx_files=tx_files,
        txouts=txouts,
        change_address=script_address,
        plutus_txins=plutus_txins,
        invalid_hereafter=invalid_hereafter,
        invalid_before=invalid_before,
        deposit=deposit_amount,
        script_valid=script_valid,
    )
    tx_signed = cluster_obj.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step2",
    )

    if not submit_tx:
        return "", tx_output

    if not script_valid:
        cluster_obj.submit_tx(tx_file=tx_signed, txins=collateral_utxos)

        assert (
            cluster_obj.get_address_balance(dst_addr.address)
            == dst_init_balance - collateral_utxos[0].amount
        ), f"Collateral was NOT spent from `{dst_addr.address}`"

        assert (
            cluster_obj.get_address_balance(script_address) == script_init_balance
        ), f"Incorrect balance for script address `{script_address}`"

        return "", tx_output

    cluster_obj.submit_tx(tx_file=tx_signed, txins=[t.txins[0] for t in tx_output.plutus_txins])

    assert (
        cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance + amount
    ), f"Incorrect balance for destination address `{dst_addr.address}`"

    assert (
        cluster_obj.get_address_balance(script_address)
        == script_init_balance - amount - tx_output.fee - deposit_amount
    ), f"Incorrect balance for script address `{script_address}`"

    for token in spent_tokens:
        assert (
            cluster_obj.get_address_balance(script_address, coin=token.coin) == 0
        ), f"Incorrect token balance for script address `{script_address}`"

    dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)

    return "", tx_output


def _create_script_context(
    cluster_obj: clusterlib.ClusterLib, redeemer_file: Path, tx_file: Optional[Path] = None
) -> None:
    """Run the `create-script-context` command (available in plutus-examples)."""
    if tx_file:
        cmd_args = [
            "create-script-context",
            "--generate-tx",
            str(tx_file),
            "--out-file",
            str(redeemer_file),
            f"--{cluster_obj.protocol}-mode",
            *cluster_obj.magic_args,
        ]
    else:
        cmd_args = ["create-script-context", "--out-file", str(redeemer_file)]

    helpers.run_command(cmd_args)
    assert redeemer_file.exists()


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALONZO,
    reason="runs only with Alonzo+ TX",
)
class TestLocking:
    """Tests for txin locking using Plutus smart contracts."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txin_locking(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Corresponds to Exercise 3 for Alonzo Blue.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tx_output_fund = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            amount=50_000_000,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")
        _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs_lock_always_suceeds[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not distutils.spawn.find_executable("create-script-context"),
        reason="cannot find `create-script-context` on the PATH",
    )
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_context_equivalance(
        self,
        cluster_lock_context_eq: clusterlib.ClusterLib,
        pool_users_lock_context_eq: List[clusterlib.PoolUser],
    ):
        """Test context equivalence while spending a locked UTxO.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * generate a dummy redeemer and a dummy Tx
        * derive the correct redeemer from the dummy Tx
        * spend the locked UTxO using the derived redeemer
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_context_eq
        temp_template = clusterlib_utils.get_temp_template(cluster)
        amount = 10_000_000
        deposit_amount = cluster.get_address_deposit()

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=pool_users_lock_context_eq[0].stake.vkey_file,
        )

        tx_files = clusterlib.TxFiles(certificate_files=[stake_addr_reg_cert_file])

        # generate a dummy redeemer in order to create a txbody from which
        # we can generate a tx and then derive the correct redeemer
        redeemer_file_dummy = Path(f"{temp_template}_dummy_script_context.redeemer")
        _create_script_context(cluster_obj=cluster, redeemer_file=redeemer_file_dummy)

        plutus_op_dummy = PlutusOp(
            script_file=CONTEXT_EQUIVALENCE_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=redeemer_file_dummy,
            execution_units=(1000_000_000, 10_000_000),
        )

        # fund the script address
        tx_output_fund = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users_lock_context_eq[0].payment,
            dst_addr=pool_users_lock_context_eq[1].payment,
            plutus_op=plutus_op_dummy,
            amount=amount,
            deposit_amount=deposit_amount,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")
        invalid_hereafter = cluster.get_slot_no() + 1000

        __, tx_output_dummy = _spend_locked_txin(
            temp_template=f"{temp_template}_dummy",
            cluster_obj=cluster,
            dst_addr=pool_users_lock_context_eq[1].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op_dummy,
            amount=amount,
            deposit_amount=deposit_amount,
            tx_files=tx_files,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
            script_valid=False,
            submit_tx=False,
        )
        assert tx_output_dummy

        # generate the "real" redeemer
        redeemer_file = Path(f"{temp_template}_script_context.redeemer")
        tx_file_dummy = Path(f"{tx_output_dummy.out_file.with_suffix('')}.signed")
        _create_script_context(
            cluster_obj=cluster, redeemer_file=redeemer_file, tx_file=tx_file_dummy
        )

        plutus_op = plutus_op_dummy._replace(redeemer_file=redeemer_file)

        _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=pool_users_lock_context_eq[1].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            deposit_amount=deposit_amount,
            tx_files=tx_files,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.parametrize(
        "script",
        (
            "guessing_game_42",  # correct datum and redeemer
            "guessing_game_42_43",  # correct datum, wrong redeemer
            "guessing_game_43_42",  # wrong datum, correct redeemer
            "guessing_game_43_43",  # wrong datum and redeemer
        ),
    )
    def test_guessing_game(
        self,
        cluster_lock_guessing_game: clusterlib.ClusterLib,
        payment_addrs_lock_guessing_game: List[clusterlib.AddressRecord],
        script: str,
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Test with with "guessing game" script that expects specific datum and redeemer value.
        Test also negative scenarios where datum or redeemer value is different than expected.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * OR check that the amount was not transferred and collateral UTxO was not spent
          when failure is expected
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_guessing_game
        temp_template = f"{clusterlib_utils.get_temp_template(cluster)}_{script}"

        if script.endswith("game_42_43"):
            datum_file = PLUTUS_DIR / "typed-42.datum"
            redeemer_file = PLUTUS_DIR / "typed-43.redeemer"
            expect_failure = True
        elif script.endswith("game_43_42"):
            datum_file = PLUTUS_DIR / "typed-43.datum"
            redeemer_file = PLUTUS_DIR / "typed-42.redeemer"
            expect_failure = True
        elif script.endswith("game_43_43"):
            datum_file = PLUTUS_DIR / "typed-43.datum"
            redeemer_file = PLUTUS_DIR / "typed-43.redeemer"
            expect_failure = True
        else:
            datum_file = PLUTUS_DIR / "typed-42.datum"
            redeemer_file = PLUTUS_DIR / "typed-42.redeemer"
            expect_failure = False

        plutus_op = PlutusOp(
            script_file=GUESSING_GAME_PLUTUS,
            datum_file=datum_file,
            redeemer_file=redeemer_file,
            execution_units=(700_000_000, 10_000_000),
        )

        tx_output_fund = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_guessing_game[0],
            dst_addr=payment_addrs_lock_guessing_game[1],
            plutus_op=plutus_op,
            amount=50_000_000,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")
        err, __ = _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs_lock_guessing_game[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=expect_failure,
        )
        if expect_failure:
            assert "ValidationTagMismatch (IsValid True)" in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_always_fails(
        self,
        cluster_lock_always_fails: clusterlib.ClusterLib,
        payment_addrs_lock_always_fails: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Test with with "always fails" script that fails for all datum / redeemer values.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred, collateral UTxO was not spent
          and the expected error was raised
        """
        cluster = cluster_lock_always_fails
        temp_template = clusterlib_utils.get_temp_template(cluster)

        plutus_op = PlutusOp(
            script_file=ALWAYS_FAILS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tx_output_fund = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            plutus_op=plutus_op,
            amount=50_000_000,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")
        err, __ = _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs_lock_always_fails[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=True,
        )
        assert "PlutusFailure" in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_script_invalid(
        self,
        cluster_lock_always_fails: clusterlib.ClusterLib,
        payment_addrs_lock_always_fails: List[clusterlib.AddressRecord],
    ):
        """Test failing script together with the `--script-invalid` argument - collateral is taken.

        Test with with "always fails" script that fails for all datum / redeemer values.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was spent
        """
        cluster = cluster_lock_always_fails
        temp_template = clusterlib_utils.get_temp_template(cluster)

        plutus_op = PlutusOp(
            script_file=ALWAYS_FAILS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tx_output_fund = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            plutus_op=plutus_op,
            amount=50_000_000,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")
        _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs_lock_always_fails[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            script_valid=False,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txin_token_locking(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        * create a Tx ouput that contains native tokens with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs_lock_always_suceeds[0]

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode("utf-8").hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=100,
        )
        tokens_rec = [Token(coin=t.token, amount=t.amount) for t in tokens]

        tx_output_fund = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            amount=50_000_000,
            tokens=tokens_rec,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")
        _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs_lock_always_suceeds[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            tokens=tokens_rec,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_collateral_w_tokens(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test spending the locked UTxO while collateral contains native tokens.

        * create a collateral UTxO with native tokens
        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs_lock_always_suceeds[0]

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode("utf-8").hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=100,
        )
        tokens_rec = [Token(coin=t.token, amount=t.amount) for t in tokens]

        tx_output_fund = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            amount=50_000_000,
            tokens_collateral=tokens_rec,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs_lock_always_suceeds[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )
        assert "CollateralContainsNonADA" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_same_collateral_txin(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test spending the locked UTxO while using the same UTxO as collateral.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO while using the same UTxO as collateral
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tx_output_fund = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            amount=50_000_000,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs_lock_always_suceeds[1],
                script_utxos=script_utxos,
                collateral_utxos=script_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )
        assert "InsufficientCollateral" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_no_datum_txin(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test using UTxO without datum hash in place of locked UTxO.

        * create a Tx ouput without a datum hash
        * try to spend the UTxO like it was locked Plutus UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)

        payment_addr = payment_addrs_lock_always_suceeds[0]
        dst_addr = payment_addrs_lock_always_suceeds[1]

        plutusrequiredtime, plutusrequiredspace = 700_000_000, 10_000_000

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(plutusrequiredtime, plutusrequiredspace),
        )

        fee_redeem = int(plutusrequiredtime + plutusrequiredspace) + 10_000_000
        collateral_fraction = cluster.get_protocol_params()["collateralPercentage"] / 100
        collateral_amount = int(fee_redeem * collateral_fraction)

        txouts = [
            clusterlib.TxOut(address=payment_addr.address, amount=50_000_000 + fee_redeem),
            clusterlib.TxOut(address=payment_addr.address, amount=collateral_amount),
        ]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_output_fund = cluster.send_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            join_txouts=False,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )
        assert "NonOutputSupplimentaryDatums" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("scenario", ("max", "max+1", "none"))
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_collaterals(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
        scenario: str,
    ):
        """Test dividing required collateral amount into multiple collateral UTxOs.

        Test 3 scenarios:
        1. maximum allowed number of collateral inputs
        2. more collateral inputs than what is allowed
        3. no collateral input

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * create multiple UTxOs for collateral
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * OR check that the amount was not transferred and collateral UTxO was not spent
          when failure is expected
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)

        max_collateral_ins = cluster.get_protocol_params()["maxCollateralInputs"]
        collateral_utxos = []

        if scenario == "max":
            collateral_num = max_collateral_ins
            exp_err = ""
        elif scenario == "max+1":
            collateral_num = max_collateral_ins + 1
            exp_err = "TooManyCollateralInputs"
        else:
            collateral_num = 0
            exp_err = "Transaction body has no collateral inputs"

        payment_addr = payment_addrs_lock_always_suceeds[4]
        dst_addr = payment_addrs_lock_always_suceeds[5]

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tx_output_fund = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
            amount=50_000_000,
        )
        fund_txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{fund_txid}#0")
        fund_collateral_utxos = cluster.get_utxo(txin=f"{fund_txid}#1")

        if collateral_num:
            # instead of using the collateral UTxO created by `_fund_script`, create multiple new
            # collateral UTxOs with the combined amount matching the original UTxO
            collateral_amount_part = int(fund_collateral_utxos[0].amount // collateral_num) + 1
            txouts_collaterals = [
                clusterlib.TxOut(address=dst_addr.address, amount=collateral_amount_part)
                for __ in range(collateral_num)
            ]
            tx_files_collaterals = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])
            tx_output_collaterals = cluster.send_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_collaterals",
                txouts=txouts_collaterals,
                tx_files=tx_files_collaterals,
                join_txouts=False,
            )
            txid_collaterals = cluster.get_txid(tx_body_file=tx_output_collaterals.out_file)
            _utxos_nested = [
                cluster.get_utxo(txin=f"{txid_collaterals}#{i}") for i in range(collateral_num)
            ]
            collateral_utxos = list(itertools.chain.from_iterable(_utxos_nested))

        if exp_err:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                _spend_locked_txin(
                    temp_template=temp_template,
                    cluster_obj=cluster,
                    dst_addr=dst_addr,
                    script_utxos=script_utxos,
                    collateral_utxos=collateral_utxos,
                    plutus_op=plutus_op,
                    amount=50_000_000,
                )
            assert exp_err in str(excinfo.value)
        else:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_collateral_percent(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Try to spend locked UTxO while collateral is less than required by `collateralPercentage`.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * create a collateral UTxO with amount of ADA less than required by `collateralPercentage`
        * try to spend the UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tx_output_fund = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            amount=50_000_000,
            collateral_fraction_offset=0.9,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs_lock_always_suceeds[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )
        assert "InsufficientCollateral" in str(excinfo.value)


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALONZO,
    reason="runs only with Alonzo+ TX",
)
class TestMinting:
    """Tests for minting using Plutus smart contracts."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_minting(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a plutus script.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a plutus script
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = clusterlib_utils.get_temp_template(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 5000_000
        token_amount = 5
        plutusrequiredtime = 700_000_000
        plutusrequiredspace = 10_000_000
        fee_step2 = int(plutusrequiredspace + plutusrequiredtime) + 10_000_000
        collateral_amount = int(fee_step2 * 1.5)

        redeemer_file = PLUTUS_DIR / "42.redeemer"

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        txouts_step1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount + fee_step2),
            # for collateral
            clusterlib.TxOut(address=issuer_addr.address, amount=collateral_amount),
        ]
        fee_step1 = cluster.calculate_tx_fee(
            src_address=payment_addr.address,
            txouts=txouts_step1,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_raw_output_step1 = cluster.build_raw_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step1,
            tx_files=tx_files_step1,
            fee=fee_step1,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_raw_output_step1.txins)

        issuer_step1_balance = cluster.get_address_balance(issuer_addr.address)
        assert (
            issuer_step1_balance
            == issuer_init_balance + lovelace_amount + fee_step2 + collateral_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # Step 2: mint the "qacoin"

        txid_step1 = cluster.get_txid(tx_body_file=tx_raw_output_step1.out_file)
        mint_utxos = cluster.get_utxo(txin=f"{txid_step1}#0")
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_step1, utxo_ix=1, amount=collateral_amount, address=issuer_addr.address
        )
        plutus_mint_data = [
            clusterlib.PlutusMint(
                txins=mint_utxos,
                collaterals=[collateral_utxo],
                script_file=MINTING_PLUTUS,
                execution_units=(plutusrequiredtime, plutusrequiredspace),
                redeemer_file=redeemer_file,
            )
        ]

        policyid = cluster.get_policyid(MINTING_PLUTUS)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint = [clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint,
        ]
        tx_raw_output_step2 = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_step2,
            tx_files=tx_files_step2,
            fee=fee_step2,
            plutus_mint=plutus_mint_data,
            mint=mint,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + collateral_amount + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_time_range_minting(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a time constraints plutus script.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a plutus script
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = clusterlib_utils.get_temp_template(cluster)
        payment_addr = payment_addrs[2]
        issuer_addr = payment_addrs[3]

        lovelace_amount = 5000_000
        token_amount = 5
        plutusrequiredtime = 700_000_000
        plutusrequiredspace = 10_000_000
        fee_step2 = int(plutusrequiredspace + plutusrequiredtime) + 10_000_000
        collateral_amount = int(fee_step2 * 1.5)

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        txouts_step1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount + fee_step2),
            # for collateral
            clusterlib.TxOut(address=issuer_addr.address, amount=collateral_amount),
        ]
        fee_step1 = cluster.calculate_tx_fee(
            src_address=payment_addr.address,
            txouts=txouts_step1,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_raw_output_step1 = cluster.build_raw_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step1,
            tx_files=tx_files_step1,
            fee=fee_step1,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_raw_output_step1.txins)

        issuer_step1_balance = cluster.get_address_balance(issuer_addr.address)
        assert (
            issuer_step1_balance
            == issuer_init_balance + lovelace_amount + fee_step2 + collateral_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # Step 2: mint the "qacoin"

        txid_step1 = cluster.get_txid(tx_body_file=tx_raw_output_step1.out_file)
        mint_utxos = cluster.get_utxo(txin=f"{txid_step1}#0")
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_step1, utxo_ix=1, amount=collateral_amount, address=issuer_addr.address
        )

        slot_step2 = cluster.get_slot_no()
        slots_offset = 1000
        timestamp_offset_ms = int(slots_offset * cluster.slot_length + 5) * 1000

        protocol_version = cluster.get_protocol_params()["protocolVersion"]["major"]
        if protocol_version > 5:
            # POSIX timestamp + offset
            redeemer_value = int(datetime.datetime.now().timestamp() * 1000) + timestamp_offset_ms
        else:
            # BUG: https://github.com/input-output-hk/cardano-node/issues/3090
            redeemer_value = 1000000000000

        plutus_mint_data = [
            clusterlib.PlutusMint(
                txins=mint_utxos,
                collaterals=[collateral_utxo],
                script_file=TIME_RANGE_PLUTUS,
                execution_units=(plutusrequiredtime, plutusrequiredspace),
                redeemer_value=str(redeemer_value),
            )
        ]

        policyid = cluster.get_policyid(TIME_RANGE_PLUTUS)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint = [clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint,
        ]
        tx_raw_output_step2 = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_step2,
            tx_files=tx_files_step2,
            fee=fee_step2,
            plutus_mint=plutus_mint_data,
            invalid_before=slot_step2 - slots_offset,
            invalid_hereafter=slot_step2 + slots_offset,
            mint=mint,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + collateral_amount + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not distutils.spawn.find_executable("create-script-context"),
        reason="cannot find `create-script-context` on the PATH",
    )
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_minting_context_equivalance(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test context equivalence while minting a token.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * generate a dummy redeemer and a dummy Tx
        * derive the correct redeemer from the dummy Tx
        * mint the token using the derived redeemer
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = clusterlib_utils.get_temp_template(cluster)
        payment_addr = payment_addrs[8]
        issuer_addr = payment_addrs[9]

        lovelace_amount = 5000_000
        token_amount = 5
        plutusrequiredtime = 800_000_000
        plutusrequiredspace = 10_000_000
        fee_step2 = int(plutusrequiredspace + plutusrequiredtime) + 10_000_000
        collateral_amount = int(fee_step2 * 1.5)

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        txouts_step1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount + fee_step2),
            # for collateral
            clusterlib.TxOut(address=issuer_addr.address, amount=collateral_amount),
        ]
        fee_step1 = cluster.calculate_tx_fee(
            src_address=payment_addr.address,
            txouts=txouts_step1,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_raw_output_step1 = cluster.build_raw_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step1,
            tx_files=tx_files_step1,
            fee=fee_step1,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_raw_output_step1.txins)

        issuer_step1_balance = cluster.get_address_balance(issuer_addr.address)
        assert (
            issuer_step1_balance
            == issuer_init_balance + lovelace_amount + fee_step2 + collateral_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # Step 2: mint the "qacoin"

        invalid_hereafter = cluster.get_slot_no() + 1000

        txid_step1 = cluster.get_txid(tx_body_file=tx_raw_output_step1.out_file)
        mint_utxos = cluster.get_utxo(txin=f"{txid_step1}#0")
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_step1, utxo_ix=1, amount=collateral_amount, address=issuer_addr.address
        )

        policyid = cluster.get_policyid(MINTING_CONTEXT_EQUIVALENCE_PLUTUS)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint = [clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file, SIGNING_KEY_GOLDEN],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint,
        ]

        # generate a dummy redeemer in order to create a txbody from which
        # we can generate a tx and then derive the correct redeemer
        redeemer_file_dummy = Path(f"{temp_template}_dummy_script_context.redeemer")
        _create_script_context(cluster_obj=cluster, redeemer_file=redeemer_file_dummy)

        plutus_mint_data_dummy = [
            clusterlib.PlutusMint(
                txins=mint_utxos,
                collaterals=[collateral_utxo],
                script_file=MINTING_CONTEXT_EQUIVALENCE_PLUTUS,
                execution_units=(plutusrequiredtime, plutusrequiredspace),
                redeemer_file=redeemer_file_dummy,
            )
        ]

        tx_output_dummy = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_dummy_tx.body",
            txouts=txouts_step2,
            tx_files=tx_files_step2,
            fee=fee_step2,
            plutus_mint=plutus_mint_data_dummy,
            required_signers=[SIGNING_KEY_GOLDEN],
            mint=mint,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
            script_valid=False,
        )
        assert tx_output_dummy

        tx_file_dummy = cluster.sign_tx(
            tx_body_file=tx_output_dummy.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_dummy",
        )

        # generate the "real" redeemer
        redeemer_file = Path(f"{temp_template}_script_context.redeemer")
        _create_script_context(
            cluster_obj=cluster, redeemer_file=redeemer_file, tx_file=tx_file_dummy
        )

        plutus_mint_data = [plutus_mint_data_dummy[0]._replace(redeemer_file=redeemer_file)]

        tx_raw_output_step2 = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_step2,
            tx_files=tx_files_step2,
            fee=fee_step2,
            plutus_mint=plutus_mint_data,
            required_signers=[SIGNING_KEY_GOLDEN],
            mint=mint,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
        )

        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + collateral_amount + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALONZO,
    reason="runs only with Alonzo+ TX",
)
class TestBuildLocking:
    """Tests for txin locking using Plutus smart contracts and `transaction build`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txin_locking(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Corresponds to Exercise 3 for Alonzo Blue.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * OR check that the amount was not transferred and collateral UTxO was spent
          when failure is expected
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[2],
            dst_addr=payment_addrs_lock_always_suceeds[3],
            plutus_op=plutus_op,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[2],
            dst_addr=payment_addrs_lock_always_suceeds[3],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not distutils.spawn.find_executable("create-script-context"),
        reason="cannot find `create-script-context` on the PATH",
    )
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_context_equivalance(
        self,
        cluster_lock_context_eq: clusterlib.ClusterLib,
        pool_users_lock_context_eq: List[clusterlib.PoolUser],
    ):
        """Test context equivalence while spending a locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * generate a dummy redeemer and a dummy Tx
        * derive the correct redeemer from the dummy Tx
        * spend the locked UTxO using the derived redeemer
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_context_eq
        temp_template = clusterlib_utils.get_temp_template(cluster)
        amount = 10_000_000
        deposit_amount = cluster.get_address_deposit()

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr2",
            stake_vkey_file=pool_users_lock_context_eq[2].stake.vkey_file,
        )

        tx_files = clusterlib.TxFiles(certificate_files=[stake_addr_reg_cert_file])

        # generate a dummy redeemer in order to create a txbody from which
        # we can generate a tx and then derive the correct redeemer
        redeemer_file_dummy = Path(f"{temp_template}_dummy_script_context.redeemer")
        _create_script_context(cluster_obj=cluster, redeemer_file=redeemer_file_dummy)

        plutus_op_dummy = PlutusOp(
            script_file=CONTEXT_EQUIVALENCE_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=redeemer_file_dummy,
        )

        # fund the script address
        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users_lock_context_eq[2].payment,
            dst_addr=pool_users_lock_context_eq[3].payment,
            plutus_op=plutus_op_dummy,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        invalid_hereafter = cluster.get_slot_no() + 1000

        __, tx_output_dummy = _build_spend_locked_txin(
            temp_template=f"{temp_template}_dummy",
            cluster_obj=cluster,
            payment_addr=pool_users_lock_context_eq[2].payment,
            dst_addr=pool_users_lock_context_eq[3].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op_dummy,
            amount=amount,
            deposit_amount=deposit_amount,
            tx_files=tx_files,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
            script_valid=False,
            submit_tx=False,
        )
        assert tx_output_dummy

        # generate the "real" redeemer
        redeemer_file = Path(f"{temp_template}_script_context.redeemer")
        tx_file_dummy = Path(f"{tx_output_dummy.out_file.with_suffix('')}.signed")
        _create_script_context(
            cluster_obj=cluster, redeemer_file=redeemer_file, tx_file=tx_file_dummy
        )

        plutus_op = plutus_op_dummy._replace(redeemer_file=redeemer_file)

        _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users_lock_context_eq[2].payment,
            dst_addr=pool_users_lock_context_eq[3].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            deposit_amount=deposit_amount,
            tx_files=tx_files,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.parametrize(
        "script",
        (
            "guessing_game_42",  # correct datum and redeemer
            "guessing_game_42_43",  # correct datum, wrong redeemer
            "guessing_game_43_42",  # wrong datum, correct redeemer
            "guessing_game_43_43",  # wrong datum and redeemer
        ),
    )
    def test_guessing_game(
        self,
        cluster_lock_guessing_game: clusterlib.ClusterLib,
        payment_addrs_lock_guessing_game: List[clusterlib.AddressRecord],
        script: str,
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with with "guessing game" script that expects specific datum and redeemer value.
        Test also negative scenarios where datum or redeemer value is different than expected.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_guessing_game
        temp_template = f"{clusterlib_utils.get_temp_template(cluster)}_{script}"

        if script.endswith("game_42_43"):
            datum_file = PLUTUS_DIR / "typed-42.datum"
            redeemer_file = PLUTUS_DIR / "typed-43.redeemer"
            expect_failure = True
        elif script.endswith("game_43_42"):
            datum_file = PLUTUS_DIR / "typed-43.datum"
            redeemer_file = PLUTUS_DIR / "typed-42.redeemer"
            expect_failure = True
        elif script.endswith("game_43_43"):
            datum_file = PLUTUS_DIR / "typed-43.datum"
            redeemer_file = PLUTUS_DIR / "typed-43.redeemer"
            expect_failure = True
        else:
            datum_file = PLUTUS_DIR / "typed-42.datum"
            redeemer_file = PLUTUS_DIR / "typed-42.redeemer"
            expect_failure = False

        plutus_op = PlutusOp(
            script_file=GUESSING_GAME_PLUTUS,
            datum_file=datum_file,
            redeemer_file=redeemer_file,
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_guessing_game[2],
            dst_addr=payment_addrs_lock_guessing_game[3],
            plutus_op=plutus_op,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        err, __ = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_guessing_game[2],
            dst_addr=payment_addrs_lock_guessing_game[3],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=expect_failure,
        )
        if expect_failure:
            assert "The Plutus script evaluation failed" in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_always_fails(
        self,
        cluster_lock_always_fails: clusterlib.ClusterLib,
        payment_addrs_lock_always_fails: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with with "always fails" script that fails for all datum / redeemer values.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the expected error was raised
        """
        cluster = cluster_lock_always_fails
        temp_template = clusterlib_utils.get_temp_template(cluster)

        plutus_op = PlutusOp(
            script_file=ALWAYS_FAILS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            plutus_op=plutus_op,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        err, __ = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=True,
        )
        assert "The Plutus script evaluation failed" in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_script_invalid(
        self,
        cluster_lock_always_fails: clusterlib.ClusterLib,
        payment_addrs_lock_always_fails: List[clusterlib.AddressRecord],
    ):
        """Test failing script together with the `--script-invalid` argument - collateral is taken.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with with "always fails" script that fails for all datum / redeemer values.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was spent
        """
        cluster = cluster_lock_always_fails
        temp_template = clusterlib_utils.get_temp_template(cluster)

        plutus_op = PlutusOp(
            script_file=ALWAYS_FAILS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            plutus_op=plutus_op,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            script_valid=False,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txin_token_locking(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx ouput that contains native tokens with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs_lock_always_suceeds[0]

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode("utf-8").hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=100,
        )
        tokens_rec = [Token(coin=t.token, amount=t.amount) for t in tokens]

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            tokens=tokens_rec,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            tokens=tokens_rec,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_collateral_w_tokens(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test spending the locked UTxO while collateral contains native tokens.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a collateral UTxO with native tokens
        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs_lock_always_suceeds[0]

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode("utf-8").hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=100,
        )
        tokens_rec = [Token(coin=t.token, amount=t.amount) for t in tokens]

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            tokens_collateral=tokens_rec,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs_lock_always_suceeds[0],
                dst_addr=payment_addrs_lock_always_suceeds[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )
        assert "CollateralContainsNonADA" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_same_collateral_txin(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test spending the locked UTxO while using the same UTxO as collateral.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO while using the same UTxO as collateral
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs_lock_always_suceeds[0],
                dst_addr=payment_addrs_lock_always_suceeds[1],
                script_utxos=script_utxos,
                collateral_utxos=script_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )
        assert "Expected key witnessed collateral" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_no_datum_txin(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test using UTxO without datum hash in place of locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx ouput without a datum hash
        * try to spend the UTxO like it was locked Plutus UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)

        payment_addr = payment_addrs_lock_always_suceeds[0]
        dst_addr = payment_addrs_lock_always_suceeds[1]

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
        )

        plutusrequiredtime, plutusrequiredspace = 700_000_000, 10_000_000
        fee_redeem = int(plutusrequiredtime + plutusrequiredspace) + 10_000_000
        collateral_fraction = cluster.get_protocol_params()["collateralPercentage"] / 100
        collateral_amount = int(fee_redeem * collateral_fraction)

        txouts = [
            clusterlib.TxOut(address=payment_addr.address, amount=50_000_000 + fee_redeem),
            clusterlib.TxOut(address=payment_addr.address, amount=collateral_amount),
        ]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_output_fund = cluster.send_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            join_txouts=False,
        )
        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )
        assert "not a Plutus script witnessed tx input" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_collateral_is_txin(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test spending the locked UTxO while using single UTxO for both collateral and Tx input.

        Uses `cardano-cli transaction build` command for building the transactions.

        Tests bug https://github.com/input-output-hk/cardano-db-sync/issues/750

        * create a Tx ouput with a datum hash at the script address and a collateral UTxO
        * check that the expected amount was locked at the script address
        * spend the locked UTxO while using the collateral UTxO both as collateral and as
          normal Tx input
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = clusterlib_utils.get_temp_template(cluster)

        payment_addr = payment_addrs_lock_always_suceeds[2]
        dst_addr = payment_addrs_lock_always_suceeds[3]

        amount = 50_000_000

        # Step 1: fund the script address

        plutus_op = PlutusOp(
            script_file=ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=PLUTUS_DIR / "typed-42.datum",
            redeemer_file=PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_step1 = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
        )

        # Step 2: spend the "locked" UTxO

        txid_step1 = cluster.get_txid(tx_body_file=tx_output_step1.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid_step1}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid_step1}#2")
        script_address = script_utxos[0].address

        script_step1_balance = cluster.get_address_balance(script_address)
        dst_step1_balance = cluster.get_address_balance(dst_addr.address)

        plutus_txins = [
            clusterlib.PlutusTxIn(
                txins=script_utxos,
                collaterals=collateral_utxos,
                script_file=plutus_op.script_file,
                datum_file=plutus_op.datum_file,
                redeemer_file=plutus_op.redeemer_file,
            )
        ]
        tx_files = clusterlib.TxFiles(
            signing_key_files=[dst_addr.skey_file],
        )
        txouts = [
            clusterlib.TxOut(address=dst_addr.address, amount=amount),
        ]

        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files,
            # `collateral_utxos` is used both as collateral and as normal Tx input
            txins=collateral_utxos,
            txouts=txouts,
            change_address=script_address,
            plutus_txins=plutus_txins,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_step2.plutus_txins]
        )

        assert (
            cluster.get_address_balance(dst_addr.address)
            == dst_step1_balance + amount - collateral_utxos[0].amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        assert (
            cluster.get_address_balance(script_address)
            == script_step1_balance - amount - tx_output_step2.fee + collateral_utxos[0].amount
        ), f"Incorrect balance for script address `{script_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALONZO,
    reason="runs only with Alonzo+ TX",
)
class TestBuildMinting:
    """Tests for minting using Plutus smart contracts and `transaction build`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_minting(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a plutus script.

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a plutus script
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        temp_template = clusterlib_utils.get_temp_template(cluster)
        payment_addr = payment_addrs[4]
        issuer_addr = payment_addrs[5]

        lovelace_amount = 5000_000
        script_fund = 1000_000_000
        collateral_fund = 1500_000_000
        token_amount = 5

        redeemer_file = PLUTUS_DIR / "42.redeemer"

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        txouts_step1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=script_fund),
            # for collateral
            clusterlib.TxOut(address=issuer_addr.address, amount=collateral_fund),
        ]

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        tx_output_step1 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            txouts=txouts_step1,
            fee_buffer=2_000_000,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        issuer_step1_balance = cluster.get_address_balance(issuer_addr.address)
        assert (
            issuer_step1_balance == issuer_init_balance + script_fund + collateral_fund
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # Step 2: mint the "qacoin"

        txid_step1 = cluster.get_txid(tx_body_file=tx_output_step1.out_file)
        mint_utxos = cluster.get_utxo(txin=f"{txid_step1}#1")
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_step1, utxo_ix=2, amount=collateral_fund, address=issuer_addr.address
        )
        plutus_mint_data = [
            clusterlib.PlutusMint(
                txins=mint_utxos,
                collaterals=[collateral_utxo],
                script_file=MINTING_PLUTUS,
                redeemer_file=redeemer_file,
            )
        ]

        policyid = cluster.get_policyid(MINTING_PLUTUS)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint = [clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint,
        ]
        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txouts=txouts_step2,
            plutus_mint=plutus_mint_data,
            mint=mint,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + collateral_fund + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_time_range_minting(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a time constraints plutus script.

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a plutus script
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = clusterlib_utils.get_temp_template(cluster)
        payment_addr = payment_addrs[6]
        issuer_addr = payment_addrs[7]

        lovelace_amount = 5000_000
        script_fund = 1000_000_000
        collateral_fund = 1500_000_000
        token_amount = 5

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        txouts_step1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=script_fund),
            # for collateral
            clusterlib.TxOut(address=issuer_addr.address, amount=collateral_fund),
        ]

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        tx_output_step1 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            txouts=txouts_step1,
            fee_buffer=2_000_000,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        issuer_step1_balance = cluster.get_address_balance(issuer_addr.address)
        assert (
            issuer_step1_balance == issuer_init_balance + script_fund + collateral_fund
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # Step 2: mint the "qacoin"

        txid_step1 = cluster.get_txid(tx_body_file=tx_output_step1.out_file)
        mint_utxos = cluster.get_utxo(txin=f"{txid_step1}#1")
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_step1, utxo_ix=2, amount=collateral_fund, address=issuer_addr.address
        )

        slot_step2 = cluster.get_slot_no()
        slots_offset = 1000
        timestamp_offset_ms = int(slots_offset * cluster.slot_length + 5) * 1000

        protocol_version = cluster.get_protocol_params()["protocolVersion"]["major"]
        if protocol_version > 5:
            # POSIX timestamp + offset
            redeemer_value = int(datetime.datetime.now().timestamp() * 1000) + timestamp_offset_ms
        else:
            # BUG: https://github.com/input-output-hk/cardano-node/issues/3090
            redeemer_value = 1000000000000

        plutus_mint_data = [
            clusterlib.PlutusMint(
                txins=mint_utxos,
                collaterals=[collateral_utxo],
                script_file=TIME_RANGE_PLUTUS,
                redeemer_value=str(redeemer_value),
            )
        ]

        policyid = cluster.get_policyid(TIME_RANGE_PLUTUS)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint = [clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint,
        ]
        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txouts=txouts_step2,
            plutus_mint=plutus_mint_data,
            invalid_before=slot_step2 - slots_offset,
            invalid_hereafter=slot_step2 + slots_offset,
            mint=mint,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + collateral_fund + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not distutils.spawn.find_executable("create-script-context"),
        reason="cannot find `create-script-context` on the PATH",
    )
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_minting_context_equivalance(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test context equivalence while minting a token.

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * generate a dummy redeemer and a dummy Tx
        * derive the correct redeemer from the dummy Tx
        * mint the token using the derived redeemer
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = clusterlib_utils.get_temp_template(cluster)
        payment_addr = payment_addrs[10]
        issuer_addr = payment_addrs[11]

        lovelace_amount = 5000_000
        script_fund = 1000_000_000
        collateral_fund = 1500_000_000
        token_amount = 5

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        txouts_step1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=script_fund),
            # for collateral
            clusterlib.TxOut(address=issuer_addr.address, amount=collateral_fund),
        ]
        tx_output_step1 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            txouts=txouts_step1,
            fee_buffer=2_000_000,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        issuer_step1_balance = cluster.get_address_balance(issuer_addr.address)
        assert (
            issuer_step1_balance == issuer_init_balance + script_fund + collateral_fund
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # Step 2: mint the "qacoin"

        invalid_hereafter = cluster.get_slot_no() + 1000

        txid_step1 = cluster.get_txid(tx_body_file=tx_output_step1.out_file)
        mint_utxos = cluster.get_utxo(txin=f"{txid_step1}#1")
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_step1, utxo_ix=2, amount=collateral_fund, address=issuer_addr.address
        )

        policyid = cluster.get_policyid(MINTING_CONTEXT_EQUIVALENCE_PLUTUS)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint = [clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file, SIGNING_KEY_GOLDEN],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint,
        ]

        # generate a dummy redeemer in order to create a txbody from which
        # we can generate a tx and then derive the correct redeemer
        redeemer_file_dummy = Path(f"{temp_template}_dummy_script_context.redeemer")
        _create_script_context(cluster_obj=cluster, redeemer_file=redeemer_file_dummy)

        plutus_mint_data_dummy = [
            clusterlib.PlutusMint(
                txins=mint_utxos,
                collaterals=[collateral_utxo],
                script_file=MINTING_CONTEXT_EQUIVALENCE_PLUTUS,
                redeemer_file=redeemer_file_dummy,
            )
        ]

        tx_output_dummy = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_dummy",
            tx_files=tx_files_step2,
            txouts=txouts_step2,
            plutus_mint=plutus_mint_data_dummy,
            required_signers=[SIGNING_KEY_GOLDEN],
            mint=mint,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
            script_valid=False,
        )
        assert tx_output_dummy

        tx_file_dummy = cluster.sign_tx(
            tx_body_file=tx_output_dummy.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_dummy",
        )

        # generate the "real" redeemer
        redeemer_file = Path(f"{temp_template}_script_context.redeemer")
        _create_script_context(
            cluster_obj=cluster, redeemer_file=redeemer_file, tx_file=tx_file_dummy
        )

        plutus_mint_data = [plutus_mint_data_dummy[0]._replace(redeemer_file=redeemer_file)]

        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txouts=txouts_step2,
            plutus_mint=plutus_mint_data,
            required_signers=[SIGNING_KEY_GOLDEN],
            mint=mint,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
        )

        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + collateral_fund + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)
