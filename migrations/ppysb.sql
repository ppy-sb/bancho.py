CREATE TABLE `scores_outer`  (
  `id` int NOT NULL,
  `server` varchar(32) CHARACTER SET utf8 COLLATE utf8_general_ci NOT NULL,
  `identifier_type` varchar(32) CHARACTER SET utf8 COLLATE utf8_general_ci NOT NULL,
  `outer_identifier` varchar(32) CHARACTER SET utf8 COLLATE utf8_general_ci NOT NULL,
  `recipient_id` int NOT NULL,
  `has_replay` tinyint(1) NOT NULL,
  `is_verified` tinyint(1) NOT NULL,
  `receipt_time` datetime NOT NULL,
  PRIMARY KEY (`id`) USING BTREE
)

CREATE TABLE `name_legality`  (
  `_id` int NOT NULL AUTO_INCREMENT,
  `id` int NOT NULL,
  `name` varchar(32) CHARACTER SET utf8 COLLATE utf8_general_ci NOT NULL,
  `name_safe` varchar(32) CHARACTER SET utf8 COLLATE utf8_general_ci NOT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT 1,
  `create_time` int NOT NULL,
  `inappropriate_check_date` datetime NULL DEFAULT NULL,
  `inappropriate_checker_version` int NOT NULL DEFAULT 0,
  `reject_reason` text CHARACTER SET utf8 COLLATE utf8_general_ci NULL,
  PRIMARY KEY (`_id`) USING BTREE,
  INDEX `name_legality_id`(`id` ASC) USING BTREE,
  INDEX `name_history_id`(`id` ASC, `is_active` ASC) USING BTREE
)
