# This file is sourced by the official mysql entrypoint after 00-create-databases.sql.
# Keep project SQL imports best-effort: a missing/partially incompatible optional
# schema must not prevent the shared dependency stack from starting.

import_required_sql() {
  local db="$1"
  local file="$2"
  mysql_note "trace real-stack: importing required ${file} into ${db}"
  docker_process_sql --database="${db}" < "${file}"
}

import_optional_sql() {
  local db="$1"
  local file="$2"
  if [ ! -s "${file}" ]; then
    mysql_warn "trace real-stack: optional SQL not found or empty: ${file}"
    return 0
  fi
  mysql_note "trace real-stack: importing optional ${file} into ${db}"
  if ! docker_process_sql --force --database="${db}" < "${file}"; then
    mysql_warn "trace real-stack: optional SQL import failed and was skipped: ${file}"
  fi
}

# Nacos tables are required for the bundled standalone Nacos sidecar.
import_required_sql nacos_config /trace-sql/nacos/nacos-mysql.sql

# Project schemas used by real-business-API validation. These are optional
# because some upstream scripts are version-specific; connection availability is
# still valuable even if a later business query fails with a table/data error.
import_optional_sql ry-cloud /trace-sql/ruoyi-plus/ry-cloud.sql
import_optional_sql ry-config /trace-sql/ruoyi-plus/ry-config.sql
import_optional_sql ry-job /trace-sql/ruoyi-plus/ry-job.sql
import_optional_sql ry-workflow /trace-sql/ruoyi-plus/ry-workflow.sql
import_optional_sql ry-seata /trace-sql/ruoyi-plus/ry-seata.sql

import_optional_sql oauth-center /trace-sql/zlt/oauth-center.sql
import_optional_sql user-center /trace-sql/zlt/user-center.sql
import_optional_sql logger-center /trace-sql/zlt/logger-center.sql
import_optional_sql file-center /trace-sql/zlt/file-center.sql

import_optional_sql ApolloConfigDB /trace-sql/apollo/apolloconfigdb.sql
import_optional_sql ApolloPortalDB /trace-sql/apollo/apolloportaldb.sql

import_optional_sql novel /trace-sql/novel/init.sql
import_optional_sql ruoyi-vue-pro /trace-sql/yudao/ruoyi-vue-pro.sql
import_optional_sql ruoyi-vue-pro /trace-sql/yudao/quartz.sql

import_optional_sql mogu_blog /trace-sql/mogublog/mogu_blog.sql
import_optional_sql mogu_picture /trace-sql/mogublog/mogu_picture.sql
import_optional_sql nacos_config /trace-sql/mogublog/nacos_config.sql

import_optional_sql cloud_admin_v1 /trace-sql/cloud-platform/init.sql

import_optional_sql lamp_none "/trace-sql/lamp-cloud/1.先执行我,创建数据库.sql"
import_optional_sql lamp_none /trace-sql/lamp-cloud/lamp_none.sql

import_optional_sql test /trace-sql/youlai/database.sql
import_optional_sql nacos_config /trace-sql/youlai/nacos_config.sql
import_optional_sql oauth2_server /trace-sql/youlai/oauth2_server.sql
import_optional_sql youlai_system /trace-sql/youlai/youlai_system.sql
import_optional_sql mall_oms /trace-sql/youlai/mall_oms.sql
import_optional_sql mall_pms /trace-sql/youlai/mall_pms.sql
import_optional_sql mall_sms /trace-sql/youlai/mall_sms.sql
import_optional_sql mall_ums /trace-sql/youlai/mall_ums.sql
import_optional_sql xxl_job /trace-sql/youlai/xxl_job.sql
