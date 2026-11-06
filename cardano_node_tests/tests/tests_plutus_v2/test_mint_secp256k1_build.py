"""SECP256k1 tests for minting with Plutus using `transaction build`."""

import logging
import pathlib as pl

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import markers
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus_v2 import mint_build
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import node_consistency

LOGGER = logging.getLogger(__name__)

pytestmark = [
    markers.SKIPIF_BUILD_UNUSABLE,
    markers.SKIPIF_PLUTUSV2_UNUSABLE,
    pytest.mark.plutus,
]


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Cache payment and issuer addresses per worker/cluster, refilling the source as needed."""
    addrs = addrs_common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=2,
        fund_idx=[0],
        amount=3_000_000_000,
        min_amount=300_000_000,
        caching_key=helpers.get_current_line_str(),
    )
    return addrs


@pytest.fixture
def issuer_funding(
    cluster: clusterlib.ClusterLib,
    payment_addrs: list[clusterlib.AddressRecord],
) -> tuple[list[clusterlib.UTXOData], list[clusterlib.UTXOData]]:
    """Fund fresh minting inputs and collateral before testing script rejection."""
    minting_cost = plutus_common.compute_cost(
        execution_cost=plutus_common.MINTING_V2_REF_COST,
        protocol_params=cluster.g_query.get_protocol_params(),
    )
    mint_utxos, collateral_utxos, *_ = mint_build._fund_issuer(
        cluster_obj=cluster,
        temp_template=common.get_test_id(cluster),
        payment_addr=payment_addrs[0],
        issuer_addr=payment_addrs[1],
        minting_cost=minting_cost,
        amount=200_000_000,
    )
    return mint_utxos, collateral_utxos


class TestSECP256k1:
    """Test SECP256k1 minting with valid and invalid signature vectors."""

    def _mint_token(
        self,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        payment_addrs: list[clusterlib.AddressRecord],
        issuer_funding: tuple[list[clusterlib.UTXOData], list[clusterlib.UTXOData]],
        script_file: pl.Path,
        redeemer_file: pl.Path,
    ) -> None:
        """Mint a token using previously funded inputs and collateral."""
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        mint_utxos, collateral_utxos = issuer_funding

        # Build the script transaction using only this test's funding outputs.
        policyid = cluster_obj.g_transaction.get_policyid(script_file)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=script_file,
                collaterals=collateral_utxos,
                redeemer_file=redeemer_file,
                policyid=policyid,
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

        tx_output_step2 = cluster_obj.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
        )

        tx_signed_step2 = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster_obj.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output_step2)
        token_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token
        )
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        node_consistency.check_tx_on_all_nodes(
            cluster_obj=cluster_obj, tx_raw_output=tx_output_step2
        )

    @allure.link(helpers.get_vcs_link())
    @markers.PARAM_PLUTUS2TO3_VERSION
    @pytest.mark.parametrize("algorithm", ("ecdsa", "schnorr"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_use_secp_builtin_functions(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        issuer_funding: tuple[list[clusterlib.UTXOData], list[clusterlib.UTXOData]],
        algorithm: str,
        plutus_version: str,
    ):
        """Test that is possible to use the two SECP256k1 builtin functions.

        * Fund the token issuer
        * Mint the tokens using a Plutus script with a SECP256k1 function
        * Check that the token was minted
        """
        temp_template = common.get_test_id(cluster)

        script_file = (
            plutus_common.MINTING_SECP256K1_ECDSA[plutus_version]
            if algorithm == "ecdsa"
            else plutus_common.MINTING_SECP256K1_SCHNORR[plutus_version]
        )

        redeemer_dir = (
            plutus_common.SEPC256K1_ECDSA_DIR
            if algorithm == "ecdsa"
            else plutus_common.SEPC256K1_SCHNORR_DIR
        )

        redeemer_file = redeemer_dir / "positive.redeemer"

        try:
            self._mint_token(
                cluster_obj=cluster,
                temp_template=temp_template,
                payment_addrs=payment_addrs,
                issuer_funding=issuer_funding,
                script_file=script_file,
                redeemer_file=redeemer_file,
            )
        except clusterlib.CLIError as err:
            before_pv8 = clusterlib_utils.get_protocol_version(cluster_obj=cluster) < 8

            # The SECP256k1 functions should work from protocol version 8
            if not before_pv8:
                raise

            # Before protocol version 8 the SECP256k1 is blocked or limited by high cost model
            err_msg = str(err)

            is_forbidden = (
                "Forbidden builtin function: (builtin "
                f"verify{algorithm.capitalize()}Secp256k1Signature)"
                in err_msg
                or f"Builtin function Verify{algorithm.capitalize()}Secp256k1Signature "
                "is not available in language PlutusV2 at and protocol version 7.0"
                in err_msg
            )

            is_overspending = (
                "The machine terminated part way through evaluation due to "
                "overspending the budget." in err_msg
            )

            if is_forbidden or is_overspending:
                pytest.xfail(
                    "The SECP256k1 builtin functions are not allowed before protocol version 8"
                )
            raise

    @allure.link(helpers.get_vcs_link())
    @markers.PARAM_PLUTUS2TO3_VERSION
    @pytest.mark.parametrize(
        "test_vector",
        ("invalid_sig", "invalid_pubkey", "no_msg", "no_pubkey", "no_sig"),
    )
    @pytest.mark.parametrize("algorithm", ("ecdsa", "schnorr"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_negative_secp_builtin_functions(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        issuer_funding: tuple[list[clusterlib.UTXOData], list[clusterlib.UTXOData]],
        test_vector: str,
        algorithm: str,
        plutus_version: str,
    ):
        """Reject invalid signature vectors after successfully funding the issuer.

        Funding failures must surface as setup errors, not expected script rejections.
        """
        temp_template = common.get_test_id(cluster)

        script_file = (
            plutus_common.MINTING_SECP256K1_ECDSA[plutus_version]
            if algorithm == "ecdsa"
            else plutus_common.MINTING_SECP256K1_SCHNORR[plutus_version]
        )

        redeemer_dir = (
            plutus_common.SEPC256K1_ECDSA_DIR
            if algorithm == "ecdsa"
            else plutus_common.SEPC256K1_SCHNORR_DIR
        )

        redeemer_file = redeemer_dir / f"{test_vector}.redeemer"

        before_pv8 = clusterlib_utils.get_protocol_version(cluster_obj=cluster) < 8

        # Capture only the script operation, never the successful funding setup.
        with pytest.raises(clusterlib.CLIError) as excinfo:
            self._mint_token(
                cluster_obj=cluster,
                temp_template=temp_template,
                payment_addrs=payment_addrs,
                issuer_funding=issuer_funding,
                script_file=script_file,
                redeemer_file=redeemer_file,
            )

        exc_value = str(excinfo.value)
        assert "MempoolTxTooSlow" not in exc_value, exc_value

        # Before protocol version 8 the SECP256k1 is blocked.
        # After that the usage is limited by high cost model.
        is_forbidden = (
            "Forbidden builtin function: (builtin "
            f"verify{algorithm.capitalize()}Secp256k1Signature)"
            in exc_value
            or f"Builtin function Verify{algorithm.capitalize()}Secp256k1Signature "
            "is not available in language PlutusV2 at and protocol version 7.0"
            in exc_value
        )

        is_overspending = (
            "The machine terminated part way through evaluation due to "
            "overspending the budget." in exc_value
        )

        if before_pv8:
            with common.allow_unstable_error_messages():
                assert is_forbidden or is_overspending, exc_value
        # From protocol version 8 the SECP256k1 functions are allowed and
        # when we provide wrong data meaningful error messages are expected.
        elif plutus_version == "v2":
            expected_error_messages = {
                "invalid_sig": "validation failed",
                "invalid_pubkey": "validation failed",
                "no_msg": (
                    "Invalid message hash" if algorithm == "ecdsa" else "Schnorr validation failed"
                ),
                "no_pubkey": "Invalid verification key",
                "no_sig": "Invalid signature",
            }
            with common.allow_unstable_error_messages():
                assert expected_error_messages[test_vector] in exc_value, exc_value
        elif plutus_version == "v3":
            # These untyped V3 scripts fail in the CEK machine, rather than emitting
            # PlutusTx.Prelude.check's PT5 trace. Malformed inputs also report builtin logs.
            validation_error = (
                "The machine terminated because of an error, "
                "either from a built-in function or from an explicit use of 'error'."
            )
            expected_error_messages = {
                "invalid_sig": validation_error,
                "invalid_pubkey": validation_error,
                "no_msg": ("Invalid message hash" if algorithm == "ecdsa" else validation_error),
                "no_pubkey": "Invalid verification key",
                "no_sig": "Invalid signature",
            }
            with common.allow_unstable_error_messages():
                assert expected_error_messages[test_vector] in exc_value, exc_value
        else:
            _msg = f"Unsupported plutus version: {plutus_version}"
            raise ValueError(_msg)
