## 请注意，务必设置达梦数据库属性
 - “大小写敏感”=否

-- nacos 数据库
CREATE SCHEMA lamp_nacos AUTHORIZATION "SYSDBA";
-- seata 数据库
CREATE SCHEMA lamp_seata AUTHORIZATION "SYSDBA";
-- SkyWalking 数据库
CREATE SCHEMA lamp_sw AUTHORIZATION "SYSDBA";

-- lamp-datasource-max 数据库
CREATE SCHEMA lamp_none AUTHORIZATION "SYSDBA";
