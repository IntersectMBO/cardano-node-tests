"""Module for exposing useful components of cluster management.

The cluster management system is designed to manage a pool of Cardano cluster instances for running
tests in parallel using `pytest-xdist`. It coordinates access to these shared cluster instances
by multiple test workers.

Key concepts:
    - **Pool of Instances**: Multiple cluster instances can be running concurrently. Each test worker
      requests a cluster instance to run a test on.
    - **Coordination via File System**: Workers communicate and coordinate through a system of status
      files created on a shared file system. These files act as locks and signals to indicate the
      state of cluster instances (e.g., which test is running, if a respin is needed, which
      resources are locked). The `status_files` module manages the creation and lookup of these
      files.
    - **Resource Management**: Tests can declare what resources they need. A resource can be, for
      example, a specific feature of a cluster that cannot be used by multiple tests at the same
      time. The `ClusterManager` handles locking of these resources so that only one test can use
      them at a time.
    - **Cluster Respin**: Some tests can modify the state of a cluster in a way that it cannot be
      used by subsequent tests. These tests can request a "respin" of the cluster instance, which
      re-initializes it to a clean state.
    - **`ClusterManager`**: This is the main class that test fixtures interact with. Its `get()`
      method is used to acquire a suitable cluster instance for a test, taking into account available
      instances, resource requirements, and scheduling priority.

This system allows for efficient parallel execution of tests that require a running Cardano
cluster, by reusing cluster instances and managing contention for shared resources.
"""

# flake8: noqa
from cardano_node_tests.cluster_management.common import CLUSTER_LOCK
from cardano_node_tests.cluster_management.manager import ClusterManager
from cardano_node_tests.cluster_management.manager import FixtureCache
from cardano_node_tests.cluster_management.resources import Resources
