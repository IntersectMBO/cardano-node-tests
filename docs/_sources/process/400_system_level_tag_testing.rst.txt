System-Level Tag Testing Overview
=================================

What (We do)
------------

For every tag of the Cardano-node, we execute a comprehensive
system-level test cycle. This includes:

*  E2E CLI tests on a local cluster setup
*  E2E CLI tests in real environments (like preview, preprod, and
   similar)
*  Sync tests starting from both a clean state and an existing one (for
   upgrade situations) on the Cardano Mainnet.

Where (Results)
---------------

*  On our 'cardano-node-tests' page, under the 'Test Results/Tag
   Testing' section, we create a distinct section for every new tag
   we're verifying.

How (We do it)
--------------

To minimize the likelihood of overlooking any details, we utilize a
checklist/template that lists all the necessary steps to be taken.

The list is broken down into the following sections:

*  **Changelogs**

   *  This section includes links to the changelogs for the specific
      component related to that particular tag.

*  **Regression Testing on a Local Cluster**

   *  This comprises a set of automated test combinations designed to
      ensure there are no regressions with the new tag.

   *  **Note**: Given the vast array of functionality combinations (such
      as P2P or legacy topologies, various cluster and transaction eras,
      etc.), we aim to mitigate risks by focusing on the most likely
      combinations.

   *  For each of these combinations, we execute all automated tests
      pertinent to those distinct scenarios.

*  **Additional Automated Testing**

   *  **Upgrade Testing**

      *  This simulates real-life situations where wallets or nodes are
         stopped, updated to the newer version, and then restarted.
      *  The procedure starts a local cluster using the most recently
         released version, runs sanity, stops the nodes, upgrades the
         cluster nodes to the designated tag version, reboots the local
         cluster, and reruns all the tests.

   *  **Rollback Testing**

      *  Testing effects of rollback on Cardano applications by breaking the consensus,
         submitting conflicting transactions, and then restoring consensus.

   *  **Block Production Testing with Mix Topology Settings (Legacy and P2P)**

      *  These tests confirm that stake pools with comparable delegated
         values but operating under different topology types (legacy or
         P2P) produce roughly the same number of blocks.

   *  **Sanity Review of the Submit-API**

      *  These tests ensure that the submit-api component remains
         compatible and functional with the indicated cardano-node tag
         version.

*  **Release Testing Checklist**

   *  Based on our past experiences, we've outlined a series of steps
      related to our actual testnets that must be completed to guarantee
      the desired quality of the tag.

*  **New Functionalities in This Tag**

   *  In this section, we present the new end-user features for the
      current tag, as derived from the changelogs. It also showcases the
      progress of validating these functionalities during the
      system-level testing phase.

*  **Known Issues**

   *  This section details a list of issues that are already known for
      this tag, specifically impacting the functionalities introduced in
      the present tag.

*  **Newly Identified Issues**

   *  Here, we list all issues uncovered during the system-level testing
      of the current tag, focusing solely on new issues rather than
      regressions.

*  **Breaking Changes**

   *  In this section, we list all the breaking changes present in the
      current tag when compared with the last released version.

**NOTE**: When the release happens to be a hard fork, we add some extra
steps in our testing process:

*  **Regression Testing on a Local Cluster**

   *  We are ensuring that all exiting to work in both eras by running
      all the automated tests both before and after the hard fork

*  **Upgrade Testing**

   *  These tests would have an extra step for executing the hard fork

How long does it take?
----------------------

Most of our system-level automated tests on a local cluster are
completed in under 2 hours. However, certain extended tests, which
involve operations and observations over more epochs, take a bit more
time.

The CLI tests are consistently refined for concurrent execution. The
tests are run in parallel across 5 distinct clusters. All tests are
triggered automatically on GitHub Actions.

The synchronization tests, which perform a full sync on Mainnet, usually
take between 1 to 2 days to run.
