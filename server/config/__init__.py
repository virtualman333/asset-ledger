"""项目配置包。

Windows 下 mysqlclient 编译困难，统一使用 PyMySQL 作为 MySQL 驱动。
"""
import pymysql

pymysql.install_as_MySQLdb()
