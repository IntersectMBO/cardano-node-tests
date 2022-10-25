-- MAINNET
-- qa_sync_tests_db.mainnet_db_sync definition

CREATE TABLE `mainnet_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `env` varchar(255) NOT NULL,
  `node_pr` varchar(255) NOT NULL,
  `db_sync_branch` varchar(255) DEFAULT NULL,
  `node_cli_version` varchar(255) NOT NULL,
  `node_git_revision` varchar(255) NOT NULL,
  `db_sync_version` varchar(255) NOT NULL,
  `db_sync_git_rev` varchar(255) NOT NULL,
  `start_test_time` varchar(255) NOT NULL,
  `end_test_time` varchar(255) NOT NULL,
  `total_sync_time_in_sec` int DEFAULT NULL,
  `total_sync_time_in_h_m_s` varchar(255) DEFAULT NULL,
  `last_synced_epoch_no` int DEFAULT NULL,
  `last_synced_block_no` int DEFAULT NULL,
  `platform_system` varchar(255) NOT NULL,
  `platform_release` varchar(255) NOT NULL,
  `platform_version` varchar(255) NOT NULL,
  `no_of_cpu_cores` int DEFAULT NULL,
  `total_ram_in_GB` int DEFAULT NULL,
  `total_database_size` varchar(100) DEFAULT NULL,
  `last_synced_slot_no` int DEFAULT NULL,
  `rollbacks` varchar(10) DEFAULT NULL,
  `errors` varchar(10) DEFAULT NULL,
  `cpu_percent_usage` decimal(10,0) DEFAULT NULL,
  `total_rss_memory_usage_in_B` bigint DEFAULT NULL,
  `node_branch` varchar(100) DEFAULT NULL,
  `node_version` varchar(100) DEFAULT NULL,
  `db_version` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.mainnet_db_sync_snapshot_creation definition

CREATE TABLE `mainnet_db_sync_snapshot_creation` (
  `identifier` varchar(255) NOT NULL,
  `env` varchar(255) NOT NULL,
  `db_sync_branch` varchar(255) DEFAULT NULL,
  `db_version` varchar(100) DEFAULT NULL,
  `db_sync_version` varchar(255) NOT NULL,
  `db_sync_git_rev` varchar(255) NOT NULL,
  `start_test_time` varchar(255) NOT NULL,
  `end_test_time` varchar(255) NOT NULL,
  `snapshot_creation_time_in_sec` int DEFAULT NULL,
  `snapshot_creation_time_in_h_m_s` varchar(255) DEFAULT NULL,
  `snapshot_size_in_mb` int DEFAULT NULL,
  `stage_2_cmd` varchar(255) NOT NULL,
  `stage_2_result` varchar(255) NOT NULL,
  `platform_system` varchar(255) NOT NULL,
  `platform_release` varchar(255) NOT NULL,
  `platform_version` varchar(255) NOT NULL,
  `no_of_cpu_cores` int DEFAULT NULL,
  `total_ram_in_GB` int DEFAULT NULL,
  PRIMARY KEY (`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.mainnet_db_sync_snapshot_restoration definition

CREATE TABLE `mainnet_db_sync_snapshot_restoration` (
  `identifier` varchar(255) NOT NULL,
  `env` varchar(255) NOT NULL,
  `node_pr` varchar(255) NOT NULL,
  `node_branch` varchar(255) NOT NULL,
  `db_sync_branch` varchar(255) DEFAULT NULL,
  `node_cli_version` varchar(255) NOT NULL,
  `node_git_revision` varchar(255) NOT NULL,
  `db_sync_version` varchar(255) NOT NULL,
  `db_sync_git_rev` varchar(255) NOT NULL,
  `start_test_time` varchar(255) NOT NULL,
  `end_test_time` varchar(255) NOT NULL,
  `node_total_sync_time_in_sec` int DEFAULT NULL,
  `node_total_sync_time_in_h_m_s` varchar(255) DEFAULT NULL,
  `db_total_sync_time_in_sec` int DEFAULT NULL,
  `db_total_sync_time_in_h_m_s` varchar(255) DEFAULT NULL,
  `snapshot_url` varchar(255) DEFAULT NULL,
  `snapshot_name` varchar(255) DEFAULT NULL,
  `snapshot_epoch_no` int DEFAULT NULL,
  `snapshot_block_no` int DEFAULT NULL,
  `snapshot_slot_no` int DEFAULT NULL,
  `last_synced_epoch_no` int DEFAULT NULL,
  `last_synced_block_no` int DEFAULT NULL,
  `last_synced_slot_no` int DEFAULT NULL,
  `cpu_percent_usage` decimal(10,0) DEFAULT NULL,
  `total_rss_memory_usage_in_B` bigint DEFAULT NULL,
  `total_database_size` varchar(255) NOT NULL,
  `rollbacks` varchar(10) NOT NULL,
  `errors` varchar(10) NOT NULL,
  `platform_system` varchar(255) NOT NULL,
  `platform_release` varchar(255) NOT NULL,
  `platform_version` varchar(255) NOT NULL,
  `no_of_cpu_cores` int DEFAULT NULL,
  `total_ram_in_GB` int DEFAULT NULL,
  `node_version` varchar(100) DEFAULT NULL,
  `db_version` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.mainnet_epoch_duration_db_sync definition

CREATE TABLE `mainnet_epoch_duration_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `epoch_no` int DEFAULT NULL,
  `sync_duration_secs` int DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.mainnet_performance_stats_db_sync definition

CREATE TABLE `mainnet_performance_stats_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `time` int DEFAULT NULL,
  `slot_no` int DEFAULT NULL,
  `cpu_percent_usage` decimal(10,0) DEFAULT NULL,
  `rss_mem_usage` bigint DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- PREPROD

-- qa_sync_tests_db.preprod_db_sync definition

CREATE TABLE `preprod_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `env` varchar(255) NOT NULL,
  `node_pr` varchar(255) NOT NULL,
  `db_sync_branch` varchar(255) DEFAULT NULL,
  `node_cli_version` varchar(255) NOT NULL,
  `node_git_revision` varchar(255) NOT NULL,
  `db_sync_version` varchar(255) NOT NULL,
  `db_sync_git_rev` varchar(255) NOT NULL,
  `start_test_time` varchar(255) NOT NULL,
  `end_test_time` varchar(255) NOT NULL,
  `total_sync_time_in_sec` int DEFAULT NULL,
  `total_sync_time_in_h_m_s` varchar(255) DEFAULT NULL,
  `last_synced_epoch_no` int DEFAULT NULL,
  `last_synced_block_no` int DEFAULT NULL,
  `platform_system` varchar(255) NOT NULL,
  `platform_release` varchar(255) NOT NULL,
  `platform_version` varchar(255) NOT NULL,
  `no_of_cpu_cores` int DEFAULT NULL,
  `total_ram_in_GB` int DEFAULT NULL,
  `cpu_percent_usage` decimal(10,0) DEFAULT NULL,
  `total_rss_memory_usage_in_B` bigint DEFAULT NULL,
  `total_database_size` varchar(100) DEFAULT NULL,
  `last_synced_slot_no` int DEFAULT NULL,
  `rollbacks` varchar(10) DEFAULT NULL,
  `errors` varchar(10) DEFAULT NULL,
  `node_branch` varchar(100) DEFAULT NULL,
  `node_version` varchar(100) DEFAULT NULL,
  `db_version` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.preprod_db_sync_snapshot_creation definition

CREATE TABLE `preprod_db_sync_snapshot_creation` (
  `identifier` varchar(255) NOT NULL,
  `env` varchar(255) NOT NULL,
  `db_sync_branch` varchar(255) DEFAULT NULL,
  `db_version` varchar(100) DEFAULT NULL,
  `db_sync_version` varchar(255) NOT NULL,
  `db_sync_git_rev` varchar(255) NOT NULL,
  `start_test_time` varchar(255) NOT NULL,
  `end_test_time` varchar(255) NOT NULL,
  `snapshot_creation_time_in_sec` int DEFAULT NULL,
  `snapshot_creation_time_in_h_m_s` varchar(255) DEFAULT NULL,
  `snapshot_size_in_mb` int DEFAULT NULL,
  `stage_2_cmd` varchar(255) NOT NULL,
  `stage_2_result` varchar(255) NOT NULL,
  `platform_system` varchar(255) NOT NULL,
  `platform_release` varchar(255) NOT NULL,
  `platform_version` varchar(255) NOT NULL,
  `no_of_cpu_cores` int DEFAULT NULL,
  `total_ram_in_GB` int DEFAULT NULL,
  PRIMARY KEY (`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.preprod_epoch_duration_db_sync definition

CREATE TABLE `preprod_epoch_duration_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `epoch_no` int DEFAULT NULL,
  `sync_duration_secs` int DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.preprod_performance_stats_db_sync definition

CREATE TABLE `preprod_performance_stats_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `time` int DEFAULT NULL,
  `slot_no` int DEFAULT NULL,
  `cpu_percent_usage` decimal(10,0) DEFAULT NULL,
  `rss_mem_usage` bigint DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- PREVIEW

-- qa_sync_tests_db.preview_db_sync definition

CREATE TABLE `preview_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `env` varchar(255) NOT NULL,
  `node_pr` varchar(255) NOT NULL,
  `db_sync_branch` varchar(255) DEFAULT NULL,
  `node_cli_version` varchar(255) NOT NULL,
  `node_git_revision` varchar(255) NOT NULL,
  `db_sync_version` varchar(255) NOT NULL,
  `db_sync_git_rev` varchar(255) NOT NULL,
  `start_test_time` varchar(255) NOT NULL,
  `end_test_time` varchar(255) NOT NULL,
  `total_sync_time_in_sec` int DEFAULT NULL,
  `total_sync_time_in_h_m_s` varchar(255) DEFAULT NULL,
  `last_synced_epoch_no` int DEFAULT NULL,
  `last_synced_block_no` int DEFAULT NULL,
  `platform_system` varchar(255) NOT NULL,
  `platform_release` varchar(255) NOT NULL,
  `platform_version` varchar(255) NOT NULL,
  `no_of_cpu_cores` int DEFAULT NULL,
  `total_ram_in_GB` int DEFAULT NULL,
  `cpu_percent_usage` decimal(10,0) DEFAULT NULL,
  `total_rss_memory_usage_in_B` bigint DEFAULT NULL,
  `total_database_size` varchar(100) DEFAULT NULL,
  `last_synced_slot_no` int DEFAULT NULL,
  `rollbacks` varchar(10) DEFAULT NULL,
  `errors` varchar(10) DEFAULT NULL,
  `node_branch` varchar(100) DEFAULT NULL,
  `node_version` varchar(100) DEFAULT NULL,
  `db_version` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.preview_db_sync_snapshot_creation definition

CREATE TABLE `preview_db_sync_snapshot_creation` (
  `identifier` varchar(255) NOT NULL,
  `env` varchar(255) NOT NULL,
  `db_sync_branch` varchar(255) DEFAULT NULL,
  `db_version` varchar(100) DEFAULT NULL,
  `db_sync_version` varchar(255) NOT NULL,
  `db_sync_git_rev` varchar(255) NOT NULL,
  `start_test_time` varchar(255) NOT NULL,
  `end_test_time` varchar(255) NOT NULL,
  `snapshot_creation_time_in_sec` int DEFAULT NULL,
  `snapshot_creation_time_in_h_m_s` varchar(255) DEFAULT NULL,
  `snapshot_size_in_mb` int DEFAULT NULL,
  `stage_2_cmd` varchar(255) NOT NULL,
  `stage_2_result` varchar(255) NOT NULL,
  `platform_system` varchar(255) NOT NULL,
  `platform_release` varchar(255) NOT NULL,
  `platform_version` varchar(255) NOT NULL,
  `no_of_cpu_cores` int DEFAULT NULL,
  `total_ram_in_GB` int DEFAULT NULL,
  PRIMARY KEY (`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.preview_epoch_duration_db_sync definition

CREATE TABLE `preview_epoch_duration_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `epoch_no` int DEFAULT NULL,
  `sync_duration_secs` int DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.preview_performance_stats_db_sync definition

CREATE TABLE `preview_performance_stats_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `time` int DEFAULT NULL,
  `slot_no` int DEFAULT NULL,
  `cpu_percent_usage` decimal(10,0) DEFAULT NULL,
  `rss_mem_usage` bigint DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- SHELLEY-QA

-- qa_sync_tests_db.`shelley-qa_db_sync` definition

CREATE TABLE `shelley-qa_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `env` varchar(255) NOT NULL,
  `node_pr` varchar(255) NOT NULL,
  `db_sync_branch` varchar(255) DEFAULT NULL,
  `node_cli_version` varchar(255) NOT NULL,
  `node_git_revision` varchar(255) NOT NULL,
  `db_sync_version` varchar(255) NOT NULL,
  `db_sync_git_rev` varchar(255) NOT NULL,
  `start_test_time` varchar(255) NOT NULL,
  `end_test_time` varchar(255) NOT NULL,
  `total_sync_time_in_sec` int DEFAULT NULL,
  `total_sync_time_in_h_m_s` varchar(255) DEFAULT NULL,
  `last_synced_epoch_no` int DEFAULT NULL,
  `last_synced_block_no` int DEFAULT NULL,
  `platform_system` varchar(255) NOT NULL,
  `platform_release` varchar(255) NOT NULL,
  `platform_version` varchar(255) NOT NULL,
  `no_of_cpu_cores` int DEFAULT NULL,
  `total_ram_in_GB` int DEFAULT NULL,
  `cpu_percent_usage` decimal(10,0) DEFAULT NULL,
  `total_rss_memory_usage_in_B` bigint DEFAULT NULL,
  `total_database_size` varchar(100) DEFAULT NULL,
  `last_synced_slot_no` int DEFAULT NULL,
  `rollbacks` varchar(10) DEFAULT NULL,
  `errors` varchar(10) DEFAULT NULL,
  `node_branch` varchar(100) DEFAULT NULL,
  `node_version` varchar(100) DEFAULT NULL,
  `db_version` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.`shelley-qa_db_sync_snapshot_creation` definition

CREATE TABLE `shelley-qa_db_sync_snapshot_creation` (
  `identifier` varchar(255) NOT NULL,
  `env` varchar(255) NOT NULL,
  `db_sync_branch` varchar(255) DEFAULT NULL,
  `db_version` varchar(100) DEFAULT NULL,
  `db_sync_version` varchar(255) NOT NULL,
  `db_sync_git_rev` varchar(255) NOT NULL,
  `start_test_time` varchar(255) NOT NULL,
  `end_test_time` varchar(255) NOT NULL,
  `snapshot_creation_time_in_sec` int DEFAULT NULL,
  `snapshot_creation_time_in_h_m_s` varchar(255) DEFAULT NULL,
  `snapshot_size_in_mb` int DEFAULT NULL,
  `stage_2_cmd` varchar(255) NOT NULL,
  `stage_2_result` varchar(255) NOT NULL,
  `platform_system` varchar(255) NOT NULL,
  `platform_release` varchar(255) NOT NULL,
  `platform_version` varchar(255) NOT NULL,
  `no_of_cpu_cores` int DEFAULT NULL,
  `total_ram_in_GB` int DEFAULT NULL,
  PRIMARY KEY (`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.`shelley-qa_epoch_duration_db_sync` definition

CREATE TABLE `shelley-qa_epoch_duration_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `epoch_no` int DEFAULT NULL,
  `sync_duration_secs` int DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- qa_sync_tests_db.`shelley-qa_performance_stats_db_sync` definition

CREATE TABLE `shelley-qa_performance_stats_db_sync` (
  `identifier` varchar(255) NOT NULL,
  `time` int DEFAULT NULL,
  `slot_no` int DEFAULT NULL,
  `cpu_percent_usage` decimal(10,0) DEFAULT NULL,
  `rss_mem_usage` bigint DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;