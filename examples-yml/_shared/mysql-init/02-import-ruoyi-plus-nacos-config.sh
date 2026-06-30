#!/usr/bin/env bash
set -eo pipefail

# RuoYi-Cloud-Plus ships placeholder rows in script/sql/ry-config.sql and keeps
# the real Nacos YAML under script/config/nacos. Populate those rows during
# MySQL init so the bundled ruoyi-nacos service can serve usable config.

CONFIG_DIR=/trace-config/ruoyi-plus/nacos

if [[ ! -d "${CONFIG_DIR}" ]]; then
  echo "[trace-real-deps] skip RuoYi-Cloud-Plus Nacos config import: ${CONFIG_DIR} not mounted"
  exit 0
fi

escape_sql_string() {
  sed -e 's/\\/\\\\/g' -e "s/'/''/g" "$1"
}

import_nacos_yaml() {
  local data_id=$1
  local file=${CONFIG_DIR}/${data_id}

  if [[ ! -f "${file}" ]]; then
    echo "[trace-real-deps] skip missing RuoYi-Cloud-Plus Nacos config ${file}"
    return 0
  fi

  local content
  content=$(escape_sql_string "${file}")

  docker_process_sql --database=ry-config <<EOSQL
SET NAMES utf8mb4;
UPDATE config_info
   SET content = '${content}',
       md5 = MD5('${content}'),
       type = 'yaml',
       gmt_modified = NOW()
 WHERE data_id = '${data_id}'
   AND group_id = 'DEFAULT_GROUP'
   AND tenant_id IN ('dev', 'prod');
EOSQL
  echo "[trace-real-deps] imported RuoYi-Cloud-Plus Nacos config ${data_id}"
}

import_nacos_yaml application-common.yml
import_nacos_yaml datasource.yml
import_nacos_yaml ruoyi-auth.yml
import_nacos_yaml ruoyi-system.yml