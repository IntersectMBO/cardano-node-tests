"""Tests for governance functionality.

Tests for update proposals are in separate file `test_update_proposals.py`.

This file tests:
* poll creation
* poll answer
* poll verification
"""
import logging
from pathlib import Path

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import poll_utils
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALONZO,
    reason="runs only with Tx era >= Alonzo",
)


@pytest.mark.smoke
class TestPoll:
    """Tests for SPO poll."""

    @pytest.fixture(scope="class")
    def governance_poll_available(self) -> None:
        if not clusterlib_utils.cli_has("governance create-poll"):
            pytest.skip("The `cardano-cli governance` poll commands are not available.")

    @pytest.fixture
    def payment_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.AddressRecord:
        """Create new payment address."""
        amount = 200_000_000

        addr = clusterlib_utils.create_payment_addr_records(
            f"chain_tx_addr_ci{cluster_manager.cluster_instance_num}",
            cluster_obj=cluster,
        )[0]

        # fund source address
        clusterlib_utils.fund_from_faucet(
            addr,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=amount,
        )

        return addr

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_create_and_answer_poll(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        governance_poll_available: None,  # noqa: ARG002
        use_build_cmd: bool,
    ):
        """Test creating and answering new SPO poll.

        * create the poll
        * publish the poll on chain
        * check that the created Tx has the expected metadata
        * answer the poll
        * publish the answer on chain
        * verify poll answer
        """
        # pylint: disable=unused-argument
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        # Publish the poll on chain

        poll_question = f"Poll {clusterlib.get_rand_str(4)}: Pineapples on pizza?"

        poll_files = poll_utils.create_poll(
            cluster_obj=cluster,
            question=poll_question,
            answers=["Yes", "No"],
            name_template=temp_template,
        )

        tx_files_poll = clusterlib.TxFiles(
            signing_key_files=[
                payment_addr.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
            metadata_json_files=[poll_files.metadata],
            metadata_json_detailed_schema=True,
        )

        if use_build_cmd:
            tx_output_poll = cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_poll",
                tx_files=tx_files_poll,
                witness_override=len(tx_files_poll.signing_key_files),
            )

            tx_signed_poll = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output_poll.out_file,
                signing_key_files=tx_files_poll.signing_key_files,
                tx_name=f"{temp_template}_poll",
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed_poll, txins=tx_output_poll.txins)
        else:
            tx_output_poll = cluster.g_transaction.send_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_poll",
                tx_files=tx_files_poll,
            )

        expected_metadata = {"94": [[0, [poll_question]], [1, [["Yes"], ["No"]]]]}

        tx_data = tx_view.load_tx_view(cluster_obj=cluster, tx_body_file=tx_output_poll.out_file)

        assert tx_data["metadata"] == expected_metadata

        out_utxos_poll = cluster.g_query.get_utxo(tx_raw_output=tx_output_poll)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos_poll, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_poll.txins) - tx_output_poll.fee
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        # Publish the answer on chain

        node_cold = cluster_manager.cache.addrs_data[cluster_management.Resources.POOL1][
            "cold_key_pair"
        ]
        pool_id = cluster.g_stake_pool.get_stake_pool_id(node_cold.vkey_file)
        pool_id_dec = helpers.decode_bech32(bech32=pool_id)

        answer_file = poll_utils.answer_poll(
            cluster_obj=cluster, poll_file=poll_files.poll, answer=1, name_template=temp_template
        )

        tx_files_answer = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file, node_cold.skey_file],
            metadata_json_files=[answer_file],
            metadata_json_detailed_schema=True,
        )

        if use_build_cmd:
            tx_output_answer = cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_answer",
                tx_files=tx_files_answer,
                required_signers=[node_cold.skey_file],
                witness_override=len(tx_files_answer.signing_key_files),
            )
            tx_signed_answer = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output_answer.out_file,
                signing_key_files=tx_files_answer.signing_key_files,
                tx_name=f"{temp_template}_answer",
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed_answer, txins=tx_output_answer.txins)
        else:
            tx_output_answer = cluster.g_transaction.send_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_answer",
                tx_files=tx_files_answer,
                required_signers=[node_cold.skey_file],
            )

        out_utxos_answer = cluster.g_query.get_utxo(tx_raw_output=tx_output_answer)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos_answer, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_answer.txins) - tx_output_answer.fee
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        # Verify the answer to the poll
        signers = poll_utils.verify_poll(
            cluster_obj=cluster, poll_file=poll_files.poll, tx_signed=tx_output_answer.out_file
        )
        assert pool_id_dec == signers[0], "The command returned unexpected signers."

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("tx_file_type", ("body", "signed"))
    def test_answer_golden_poll(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        governance_poll_available: None,  # noqa: ARG002
        tx_file_type: str,
        use_build_cmd: bool,
    ):
        """Test answering a golden SPO poll.

        * create an answer to a poll
        * create Tx with the answer
        * verify poll answer
        """
        # pylint: disable=unused-argument
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}_{tx_file_type}"

        poll_file = DATA_DIR / "governance_poll.json"
        spo_signing_key = DATA_DIR / "golden_stake_pool.skey"
        stake_pool_id = "f8db28823f8ebd01a2d9e24efb2f0d18e387665770274513e370b5d5"

        # Create an answer to the poll
        answer_file = poll_utils.answer_poll(
            cluster_obj=cluster, poll_file=poll_file, answer=1, name_template=temp_template
        )

        # Create Tx with the answer
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file, spo_signing_key],
            metadata_json_files=[answer_file],
            metadata_json_detailed_schema=True,
        )

        if use_build_cmd:
            tx_output = cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_answer",
                tx_files=tx_files,
                required_signers=[spo_signing_key],
            )
        else:
            tx_output = cluster.g_transaction.build_raw_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_answer",
                tx_files=tx_files,
                fee=10_000,
                required_signers=[spo_signing_key],
            )

        if tx_file_type == "body":
            check_tx_file = tx_output.out_file
        else:
            check_tx_file = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )

        # Verify the answer to the poll
        signers = poll_utils.verify_poll(
            cluster_obj=cluster, poll_file=poll_file, tx_signed=check_tx_file
        )
        assert stake_pool_id == signers[0], "The command returned unexpected signers."

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        answer_index=st.integers(min_value=-common.MAX_INT64, max_value=common.MAX_INT64)
    )
    @hypothesis.example(answer_index=-common.MAX_INT64)
    @hypothesis.example(answer_index=common.MAX_INT64)
    @common.hypothesis_settings(max_examples=1000)
    def test_create_invalid_answer(
        self,
        cluster: clusterlib.ClusterLib,
        governance_poll_available: None,  # noqa: ARG002
        answer_index: int,
    ):
        """Test answering an SPO poll with invalid answer.

        Expect failure.
        """
        # pylint: disable=unused-argument
        hypothesis.assume(answer_index not in (0, 1))

        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"
        poll_file = DATA_DIR / "governance_poll.json"

        with pytest.raises(clusterlib.CLIError) as excinfo:
            poll_utils.answer_poll(
                cluster_obj=cluster,
                poll_file=poll_file,
                answer=answer_index,
                name_template=temp_template,
            )

        err_str = str(excinfo.value)
        assert "Poll answer out of bounds" in err_str or "negative index" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("tx_file_type", ("body", "signed"))
    def test_verify_answer_without_required_signer(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        governance_poll_available: None,  # noqa: ARG002
        tx_file_type: str,
        use_build_cmd: bool,
    ):
        """Test verifying an answer to an SPO poll without valid required signer.

        Expect failure.
        """
        # pylint: disable=unused-argument
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}_{tx_file_type}"
        poll_file = DATA_DIR / "governance_poll.json"

        # Create answer
        answer_file = poll_utils.answer_poll(
            cluster_obj=cluster, poll_file=poll_file, answer=1, name_template=temp_template
        )

        # Create Tx with an answer to the poll
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[answer_file],
            metadata_json_detailed_schema=True,
        )

        if use_build_cmd:
            tx_output = cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_publish_answer_build",
                tx_files=tx_files,
                witness_override=len(tx_files.signing_key_files),
            )
        else:
            tx_output = cluster.g_transaction.build_raw_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_answer",
                tx_files=tx_files,
                fee=10_000,
            )

        if tx_file_type == "body":
            check_tx_file = tx_output.out_file
        else:
            check_tx_file = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )

        # Verify answer without required signers
        with pytest.raises(clusterlib.CLIError) as excinfo:
            poll_utils.verify_poll(
                cluster_obj=cluster, poll_file=poll_file, tx_signed=check_tx_file
            )

        err_str = str(excinfo.value)
        assert (
            "Signatories MUST be specified as extra signatories on the transaction "
            "and cannot be mere payment keys"
        ) in err_str, err_str
