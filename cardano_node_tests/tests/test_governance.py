"""Tests for governance commands."""
import json
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
from cardano_node_tests.utils import tx_view

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"


@pytest.mark.smoke
class TestGovernancePoll:
    """Tests for cardano-cli governance poll commands."""

    MAX_INT64 = (2**63) - 1

    @pytest.fixture(scope="class")
    def governance_poll_available(self) -> None:
        if not (
            clusterlib_utils.cli_has("governance create-poll")
            or clusterlib_utils.cli_has("governance answer-poll")
            or clusterlib_utils.cli_has("governance verify-poll")
        ):
            pytest.skip("CLI commands `governance` poll are not available")

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
    def test_create_poll(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        governance_poll_available: None,  # noqa: ARG002
        use_build_cmd: bool,
    ):
        """Test 'governance create-poll'.

        * create the poll
        * check that the expected outfile is created
        * publish the poll on chain
        * check that the created tx has the expected metadata
        """
        # pylint: disable=unused-argument
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        poll_file = f"{temp_template}_poll.json"

        poll_question = f"Poll {clusterlib.get_rand_str(4)}: Pineapples on pizza?"

        cli_out = cluster.cli(
            [
                "governance",
                "create-poll",
                "--question",
                poll_question,
                "--answer",
                "Yes",
                "--answer",
                "No",
                "--out-file",
                poll_file,
            ]
        )

        assert "Poll created successfully" in cli_out.stderr.decode("utf-8")

        # Publish poll
        poll_metadata_file = Path(f"{temp_template}_poll_metadata.json")
        with open(poll_metadata_file, "w", encoding="utf-8") as out_json:
            json.dump(json.loads(cli_out.stdout.rstrip().decode("utf-8")), out_json)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[
                payment_addr.skey_file,
                *cluster.g_genesis.genesis_keys.delegate_skeys,
            ],
            metadata_json_files=[poll_metadata_file],
            metadata_json_detailed_schema=True,
        )

        if use_build_cmd:
            tx_output = cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_publish_poll_build",
                tx_files=tx_files,
                witness_override=len(tx_files.signing_key_files),
            )

            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)
        else:
            tx_output = cluster.g_transaction.send_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_publish_poll_build_raw",
                tx_files=tx_files,
            )

        expected_metadata = {"94": [[0, [poll_question]], [1, [["Yes"], ["No"]]]]}

        tx_data = tx_view.load_tx_view(cluster_obj=cluster, tx_body_file=tx_output.out_file)

        assert tx_data["metadata"] == expected_metadata

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_answer_poll(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        governance_poll_available: None,  # noqa: ARG002
        use_build_cmd: bool,
    ):
        """Test 'governance answer-poll' and 'governance verify-poll'.

        * create answer
        * check if the answer was created successfully
        * publish answer on chain
        * verify poll answer
        """
        # pylint: disable=unused-argument
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        poll_file = DATA_DIR / "governance_poll.json"
        spo_signing_key = DATA_DIR / "golden_stake_pool.skey"
        stake_pool_id = "f8db28823f8ebd01a2d9e24efb2f0d18e387665770274513e370b5d5"

        # Create answer
        answer_output = cluster.cli(
            [
                "governance",
                "answer-poll",
                "--poll-file",
                str(poll_file),
                "--answer",
                "1",
            ]
        )

        assert "Poll answer created successfully" in answer_output.stderr.decode("utf-8")

        answer_file = Path(f"{temp_template}.json")
        with open(answer_file, "w", encoding="utf-8") as out_json:
            json.dump(json.loads(answer_output.stdout.rstrip().decode("utf-8")), out_json)

        # Publish answer
        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file, spo_signing_key],
            metadata_json_files=[answer_file],
            metadata_json_detailed_schema=True,
        )

        if use_build_cmd:
            tx_output = cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_publish_answer_build",
                tx_files=tx_files,
                required_signers=[spo_signing_key],
            )

            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)
        else:
            tx_output = cluster.g_transaction.send_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_publish_answer_build_raw",
                tx_files=tx_files,
                required_signers=[spo_signing_key],
            )

        # Verify answer
        answer_verified = cluster.cli(
            [
                "governance",
                "verify-poll",
                "--poll-file",
                str(poll_file),
                "--signed-tx-file",
                str(tx_output.out_file),
            ]
        )

        assert (
            "Found valid poll answer, signed by:" in answer_verified.stderr.decode("utf-8")
            and stake_pool_id == json.loads(answer_verified.stdout.decode("utf-8"))[0]
        ), "The answer is invalid."

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(answer_index=st.integers(min_value=2, max_value=MAX_INT64))
    @common.hypothesis_settings(max_examples=300)
    def test_create_invalid_answer(
        self,
        cluster: clusterlib.ClusterLib,
        governance_poll_available: None,  # noqa: ARG002
        answer_index: int,
    ):
        """Test 'governance answer-poll' with invalid answer.

        * Expect failure.
        """
        # pylint: disable=unused-argument
        common.get_test_id(cluster)

        poll_file = DATA_DIR / "governance_poll.json"

        # Create answer
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "governance",
                    "answer-poll",
                    "--poll-file",
                    str(poll_file),
                    "--answer",
                    str(answer_index),
                ]
            )

        err_str = str(excinfo.value)

        assert "Poll answer out of bounds" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_verify_answer_without_required_signer(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        governance_poll_available: None,  # noqa: ARG002
        use_build_cmd: bool,
    ):
        """Test 'governance verify-poll' with an answer without valid required signer.

        * Expect failure.
        """
        # pylint: disable=unused-argument
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        poll_file = DATA_DIR / "governance_poll.json"

        # Create answer
        answer_output = cluster.cli(
            [
                "governance",
                "answer-poll",
                "--poll-file",
                str(poll_file),
                "--answer",
                "1",
            ]
        )

        assert "Poll answer created successfully" in answer_output.stderr.decode("utf-8")

        answer_file = Path(f"{temp_template}.json")
        with open(answer_file, "w", encoding="utf-8") as out_json:
            json.dump(json.loads(answer_output.stdout.rstrip().decode("utf-8")), out_json)

        # Publish answer
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

            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)
        else:
            tx_output = cluster.g_transaction.send_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_publish_answer_build_raw",
                tx_files=tx_files,
            )

        # Verify answer without required signers
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "governance",
                    "verify-poll",
                    "--poll-file",
                    str(poll_file),
                    "--signed-tx-file",
                    str(tx_output.out_file),
                ]
            )

        err_str = str(excinfo.value)

        assert (
            "Signatories MUST be specified as extra signatories on the transaction "
            "and cannot be mere payment keys"
        ) in err_str, err_str
