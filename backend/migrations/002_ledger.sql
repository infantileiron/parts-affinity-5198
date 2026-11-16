-- 002：按作业登记领用/退料流水（solo-2611）
-- 首次启动 ORM 也会建表；本文件保存实际 DDL，供既有库升级。

ALTER TABLE `jobs`
  ADD COLUMN `ledger_state` varchar(20) NOT NULL DEFAULT 'manual' AFTER `note`,
  ADD COLUMN `settled_at` varchar(40) DEFAULT NULL AFTER `ledger_state`,
  ADD COLUMN `ledger_version` int NOT NULL DEFAULT '0' AFTER `settled_at`;

CREATE TABLE `ledger_entries` (
  `id` int NOT NULL AUTO_INCREMENT,
  `entry_no` varchar(64) NOT NULL,
  `job_id` int NOT NULL,
  `direction` varchar(10) NOT NULL,
  `part` varchar(40) NOT NULL,
  `qty` int NOT NULL,
  `returned_qty` int NOT NULL DEFAULT '0',
  `ref_entry_no` varchar(64) DEFAULT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'booked',
  `reject_reason` text,
  `created_by` varchar(100) DEFAULT NULL,
  `created_at` varchar(40) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ledger_entry_no` (`entry_no`),
  KEY `ix_ledger_entries_job_id` (`job_id`),
  KEY `ix_ledger_entries_ref_entry_no` (`ref_entry_no`),
  CONSTRAINT `ledger_entries_ibfk_1` FOREIGN KEY (`job_id`) REFERENCES `jobs` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
