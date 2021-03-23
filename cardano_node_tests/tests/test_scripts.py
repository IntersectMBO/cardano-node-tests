"""Tests for multisig transactions and scripts.

* multisig
* time locking
* auxiliary scripts
"""
import json
import logging
import random
from pathlib import Path
from typing import List
from typing import Optional

import allure
import cbor2
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"


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


def multisig_tx(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    src_address: str,
    dst_address: str,
    amount: int,
    multisig_script: Path,
    payment_skey_files: List[Path],
    invalid_hereafter: Optional[int] = None,
    invalid_before: Optional[int] = None,
    script_is_src=False,
):
    """Build and submit multisig transaction."""
    # record initial balances
    src_init_balance = cluster_obj.get_address_balance(src_address)
    dst_init_balance = cluster_obj.get_address_balance(dst_address)

    # create TX body
    destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
    witness_count_add = len(payment_skey_files)
    if script_is_src:
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
        witness_count_add += 5

    ttl = cluster_obj.calculate_tx_ttl()
    fee = cluster_obj.calculate_tx_fee(
        src_address=src_address,
        tx_name=temp_template,
        txouts=destinations,
        ttl=ttl,
        witness_count_add=witness_count_add,
    )
    tx_raw_output = cluster_obj.build_raw_tx(
        src_address=src_address,
        tx_name=temp_template,
        txouts=destinations,
        fee=fee,
        ttl=ttl,
        invalid_hereafter=invalid_hereafter,
        invalid_before=invalid_before,
    )

    # create witness file for each key
    witness_files = [
        cluster_obj.witness_tx(
            tx_body_file=tx_raw_output.out_file,
            witness_name=f"{temp_template}_skey{idx}",
            signing_key_files=[skey],
        )
        for idx, skey in enumerate(payment_skey_files)
    ]
    if script_is_src:
        witness_files.append(
            cluster_obj.witness_tx(
                tx_body_file=tx_raw_output.out_file,
                witness_name=f"{temp_template}_script",
                script_file=multisig_script,
            )
        )

    # sign TX using witness files
    tx_witnessed_file = cluster_obj.assemble_tx(
        tx_body_file=tx_raw_output.out_file,
        witness_files=witness_files,
        tx_name=temp_template,
    )

    # submit signed TX
    cluster_obj.submit_tx(tx_witnessed_file)
    cluster_obj.wait_for_new_block(new_blocks=2)

    # check final balances
    assert (
        cluster_obj.get_address_balance(src_address)
        == src_init_balance - tx_raw_output.fee - amount
    ), f"Incorrect balance for source address `{src_address}`"

    assert (
        cluster_obj.get_address_balance(dst_address) == dst_init_balance + amount
    ), f"Incorrect balance for script address `{dst_address}`"


@pytest.mark.testnets
class TestBasic:
    """Basic tests for multisig transactions."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[f"multi_addr_ci{cluster_manager.cluster_instance}_{i}" for i in range(20)],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=20_000_000,
        )

        return addrs

    @allure.link(helpers.get_vcs_link())
    def test_script_addr_length(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Check that script address length is the same as lenght of other addresses.

        There was an issue that script address was 32 bytes instead of 28 bytes.
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # check script address length
        assert len(script_address) == len(payment_addrs[0].address)

    @allure.link(helpers.get_vcs_link())
    def test_multisig_all(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send funds to and from script address using the *all* script."""
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=2_000_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            src_address=script_address,
            dst_address=payment_addrs[0].address,
            amount=1000,
            multisig_script=multisig_script,
            payment_skey_files=payment_skey_files,
            script_is_src=True,
        )

    @allure.link(helpers.get_vcs_link())
    def test_multisig_any(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send funds using the *any* script.

        * send funds to script address
        * send funds from script address using single witness
        * send funds from script address using multiple witnesses
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        skeys_len = len(payment_skey_files)

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=5_000_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address using single witness
        for i in range(5):
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_single_{i}",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1000,
                multisig_script=multisig_script,
                payment_skey_files=[payment_skey_files[random.randrange(0, skeys_len)]],
                script_is_src=True,
            )

        # send funds from script address using multiple witnesses
        for i in range(5):
            num_of_skeys = random.randrange(2, skeys_len)
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_multi_{i}",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1000,
                multisig_script=multisig_script,
                payment_skey_files=random.sample(payment_skey_files, k=num_of_skeys),
                script_is_src=True,
            )

    @allure.link(helpers.get_vcs_link())
    def test_multisig_atleast(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send funds to and from script address using the *atLeast* script."""
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        skeys_len = len(payment_skey_files)
        required = skeys_len - 4

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.AT_LEAST,
            payment_vkey_files=payment_vkey_files,
            required=required,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=2_000_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address
        for i in range(5):
            num_of_skeys = random.randrange(required, skeys_len)
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_{i}",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1000,
                multisig_script=multisig_script,
                payment_skey_files=random.sample(payment_skey_files, k=num_of_skeys),
                script_is_src=True,
            )

    @allure.link(helpers.get_vcs_link())
    def test_normal_tx_to_script_addr(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send funds to script address using TX signed with skeys (not using witness files)."""
        temp_template = helpers.get_func_name()
        src_address = payment_addrs[0].address
        amount = 1000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=[p.vkey_file for p in payment_addrs],
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # record initial balances
        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(script_address)

        # send funds to script address
        destinations = [clusterlib.TxOut(address=script_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_raw_output = cluster.send_funds(
            src_address=src_address,
            tx_name=temp_template,
            destinations=destinations,
            tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        # check final balances
        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(script_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{script_address}`"

    @allure.link(helpers.get_vcs_link())
    def test_normal_tx_from_script_addr(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send funds from script address using TX signed with skeys (not using witness files)."""
        temp_template = helpers.get_func_name()
        dst_addr = payment_addrs[1]
        amount = 1000

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=temp_template,
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=300_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # record initial balances
        src_init_balance = cluster.get_address_balance(script_address)
        dst_init_balance = cluster.get_address_balance(dst_addr.address)

        # send funds from script address
        destinations = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])

        tx_raw_output = cluster.send_tx(
            src_address=script_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            script_files=[multisig_script],
        )
        cluster.wait_for_new_block(new_blocks=2)

        # check final balances
        assert (
            cluster.get_address_balance(script_address)
            == src_init_balance - tx_raw_output.fee - amount
        ), f"Incorrect balance for script address `{script_address}`"

        assert (
            cluster.get_address_balance(dst_addr.address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

    @allure.link(helpers.get_vcs_link())
    def test_multisig_empty_all(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send funds from script address using the *all* script with zero skeys."""
        temp_template = helpers.get_func_name()

        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=(),
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=2_000_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            src_address=script_address,
            dst_address=payment_addrs[0].address,
            amount=1000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
            script_is_src=True,
        )

    @allure.link(helpers.get_vcs_link())
    def test_multisig_no_required_atleast(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send funds from script address using the *atLeast* script with no required witnesses."""
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.AT_LEAST,
            payment_vkey_files=payment_vkey_files,
            required=0,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=2_000_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            src_address=script_address,
            dst_address=payment_addrs[0].address,
            amount=1000,
            multisig_script=multisig_script,
            payment_skey_files=[],
            script_is_src=True,
        )


@pytest.mark.testnets
class TestNegative:
    """Transaction tests that are expected to fail."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[f"multi_neg_addr_ci{cluster_manager.cluster_instance}_{i}" for i in range(10)],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return addrs

    @allure.link(helpers.get_vcs_link())
    def test_multisig_all_missing_skey(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Try to send funds from script address using the *all* script, omit one skey.

        Expect failure.
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=temp_template,
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=300_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address, omit one skey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=temp_template,
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1000,
                multisig_script=multisig_script,
                payment_skey_files=payment_skey_files[:-1],
                script_is_src=True,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_multisig_any_unlisted_skey(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Try to send funds from script address using the *any* script with unlisted skey.

        Expect failure.
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs[:-1]]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=temp_template,
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=300_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address, use skey that is not listed in the script
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=temp_template,
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1000,
                multisig_script=multisig_script,
                payment_skey_files=[payment_skey_files[-1]],
                script_is_src=True,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_multisig_atleast_low_num_of_skeys(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Try to send funds from script address using the *atLeast* script.

        Num of skeys < required. Expect failure.
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        skeys_len = len(payment_skey_files)
        required = skeys_len - 4

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.AT_LEAST,
            payment_vkey_files=payment_vkey_files,
            required=required,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=temp_template,
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=300_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address, use lower number of skeys then required
        for num_of_skeys in range(required):
            with pytest.raises(clusterlib.CLIError) as excinfo:
                multisig_tx(
                    cluster_obj=cluster,
                    temp_template=temp_template,
                    src_address=script_address,
                    dst_address=payment_addrs[0].address,
                    amount=1000,
                    multisig_script=multisig_script,
                    payment_skey_files=random.sample(payment_skey_files, k=num_of_skeys),
                    script_is_src=True,
                )
            assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)


@pytest.mark.testnets
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALLEGRA or VERSIONS.node < version.parse("1.24.0"),
    reason="runs on version >= 1.24.0 and with Allegra+ TX",
)
class TestTimeLocking:
    """Tests for time locking."""

    # TODO: convert to property-based tests

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[
                    f"multi_addr_time_locking_ci{cluster_manager.cluster_instance}_{i}"
                    for i in range(20)
                ],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=20_000_000,
        )

        return addrs

    @allure.link(helpers.get_vcs_link())
    def test_script_after(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Check that it is possible to spend from script address after given slot."""
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=100,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=2_000_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            src_address=script_address,
            dst_address=payment_addrs[0].address,
            amount=1000,
            multisig_script=multisig_script,
            payment_skey_files=payment_skey_files,
            invalid_before=100,
            invalid_hereafter=cluster.get_slot_no() + 1000,
            script_is_src=True,
        )

    @allure.link(helpers.get_vcs_link())
    def test_script_before(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Check that it is possible to spend from script address before given slot."""
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        before_slot = cluster.get_slot_no() + 10_000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=2_000_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            src_address=script_address,
            dst_address=payment_addrs[0].address,
            amount=1000,
            multisig_script=multisig_script,
            payment_skey_files=payment_skey_files,
            invalid_before=100,
            invalid_hereafter=cluster.get_slot_no() + 1000,
            script_is_src=True,
        )

    @allure.link(helpers.get_vcs_link())
    def test_before_past(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Check that it's NOT possible to spend from the script address.

        The "before" slot is in the past.
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        before_slot = cluster.get_slot_no() - 1

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=500_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address - valid range, slot is already in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=10,
                multisig_script=multisig_script,
                payment_skey_files=payment_skey_files,
                invalid_before=1,
                invalid_hereafter=before_slot,
                script_is_src=True,
            )
        assert "OutsideValidityIntervalUTxO" in str(excinfo.value)

        # send funds from script address - invalid range, slot is already in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=10,
                multisig_script=multisig_script,
                payment_skey_files=payment_skey_files,
                invalid_before=1,
                invalid_hereafter=before_slot + 1,
                script_is_src=True,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_before_future(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Check that it's NOT possible to spend from the script address.

        The "before" slot is in the future and the given range is invalid.
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        before_slot = cluster.get_slot_no() + 10_000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=500_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address - invalid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=10,
                multisig_script=multisig_script,
                payment_skey_files=payment_skey_files,
                invalid_before=1,
                invalid_hereafter=before_slot + 1,
                script_is_src=True,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_after_future(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Check that it's NOT possible to spend from the script address.

        The "after" slot is in the future and the given range is invalid.
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        after_slot = cluster.get_slot_no() + 10_000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=after_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=500_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address - valid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=10,
                multisig_script=multisig_script,
                payment_skey_files=payment_skey_files,
                invalid_before=after_slot,
                invalid_hereafter=after_slot + 100,
                script_is_src=True,
            )
        assert "OutsideValidityIntervalUTxO" in str(excinfo.value)

        # send funds from script address - invalid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=10,
                multisig_script=multisig_script,
                payment_skey_files=payment_skey_files,
                invalid_before=1,
                invalid_hereafter=after_slot,
                script_is_src=True,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_after_past(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Check that it's NOT possible to spend from the script address.

        The "after" slot is in the past.
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        after_slot = cluster.get_slot_no() - 1

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=after_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # create script address
        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=multisig_script
        )

        # send funds to script address
        multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=500_000,
            multisig_script=multisig_script,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address - valid slot,
        # invalid range - `invalid_hereafter` is in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=10,
                multisig_script=multisig_script,
                payment_skey_files=payment_skey_files,
                invalid_before=1,
                invalid_hereafter=after_slot,
                script_is_src=True,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)


@pytest.mark.testnets
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALLEGRA or VERSIONS.node < version.parse("1.24.0"),
    reason="runs on version >= 1.24.0 and with Allegra+ TX",
)
class TestAuxiliaryScripts:
    """Tests for auxiliary scripts."""

    JSON_METADATA_FILE = DATA_DIR / "tx_metadata.json"
    CBOR_METADATA_FILE = DATA_DIR / "tx_metadata.cbor"

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[
                    f"multi_addr_aux_scripts_ci{cluster_manager.cluster_instance}_{i}"
                    for i in range(20)
                ],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=20_000_000,
        )

        return addrs

    @allure.link(helpers.get_vcs_link())
    def test_tx_script_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send transaction with auxiliary script and metadata JSON.

        Check that the auxiliary script is present in the TX body.
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=10,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
            script_files=[multisig_script],
        )
        tx_raw_output = cluster.send_tx(
            src_address=payment_addrs[0].address, tx_name=temp_template, tx_files=tx_files
        )
        cluster.wait_for_new_block(new_blocks=2)
        assert tx_raw_output.fee, "Transaction had no fee"

        with open(tx_raw_output.out_file) as body_fp:
            tx_body_json = json.load(body_fp)

        cbor_body = bytes.fromhex(tx_body_json["cborHex"])
        cbor_body_metadata = cbor2.loads(cbor_body)[1]

        cbor_body_script = cbor_body_metadata[1]
        assert cbor_body_script, "Auxiliary script not present"

    @allure.link(helpers.get_vcs_link())
    def test_tx_script_metadata_cbor(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send transaction with auxiliary script and metadata CBOR.

        Check that the auxiliary script is present in the TX body.
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.AT_LEAST,
            payment_vkey_files=payment_vkey_files,
            required=2,
            slot=1000,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
            metadata_cbor_files=[self.CBOR_METADATA_FILE],
            script_files=[multisig_script],
        )
        tx_raw_output = cluster.send_tx(
            src_address=payment_addrs[0].address, tx_name=temp_template, tx_files=tx_files
        )
        cluster.wait_for_new_block(new_blocks=2)
        assert tx_raw_output.fee, "Transaction had no fee"

        with open(tx_raw_output.out_file) as body_fp:
            tx_body_json = json.load(body_fp)

        cbor_body = bytes.fromhex(tx_body_json["cborHex"])
        cbor_body_metadata = cbor2.loads(cbor_body)[1]

        cbor_body_script = cbor_body_metadata[1]
        assert cbor_body_script, "Auxiliary script not present"

    @allure.link(helpers.get_vcs_link())
    def test_tx_script_no_metadata(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send transaction with auxiliary script and no other metadata.

        Check that the auxiliary script is present in the TX body.
        """
        temp_template = helpers.get_func_name()

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
            script_files=[multisig_script],
        )
        tx_raw_output = cluster.send_tx(
            src_address=payment_addrs[0].address, tx_name=temp_template, tx_files=tx_files
        )
        cluster.wait_for_new_block(new_blocks=2)
        assert tx_raw_output.fee, "Transaction had no fee"

        with open(tx_raw_output.out_file) as body_fp:
            tx_body_json = json.load(body_fp)

        cbor_body = bytes.fromhex(tx_body_json["cborHex"])
        cbor_body_metadata = cbor2.loads(cbor_body)[1]

        cbor_body_script = cbor_body_metadata[1]
        assert cbor_body_script, "Auxiliary script not present"

    @allure.link(helpers.get_vcs_link())
    def test_tx_script_invalid(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Build transaction with invalid auxiliary script.

        Expect failure.
        """
        temp_template = helpers.get_func_name()

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
            script_files=[self.JSON_METADATA_FILE],  # not valid script file
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=payment_addrs[0].address, tx_name=temp_template, tx_files=tx_files
            )
        assert 'Error in $: key "type" not found' in str(excinfo.value)
