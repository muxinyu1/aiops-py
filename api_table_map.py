"""
api_table_map.py — 静态分析: API → 数据库表映射

三种 ORM 来源:
  1. MyBatis XML:  namespace(Mapper FQCN) → <select|insert|update|delete> 内 SQL 表名
  2. MyBatis-Plus: @TableName("xxx") 注解 (BaseMapper 内置 CRUD, 实体注解是唯一来源)
  3. JPA:          @Table(name="xxx")

关联: Mapper FQCN / 实体类 → Joern callgraph 节点 (className 前缀匹配)
      → 沿 controller→service→mapper 链路, 得到每个 HTTP API 的 tables_rw。

输出: api_table_map.json
  { "POST /menu": {"tables_rw": [...], "chain": [...], "param_fields": {...}} }
"""

from __future__ import annotations

import json
import os
import re
import glob
import xml.etree.ElementTree as ET
from collections import defaultdict

PROJECTS = ["RuoYi-Cloud-Plus", "pig", "yudao-cloud", "lamp-cloud", "SpringBlade", "mall-swarm"]
SQL_TABLE_RE = re.compile(
    r"\b(?:from|join|into|update)\s+`?([a-zA-Z_][\w]*)`?", re.I
)
SQL_TABLE_RE2 = re.compile(r"\b(?:from|join|into|update)\s+`?([\w]+)`?\s", re.I)


# ── 1. MyBatis XML 解析 ──────────────────────────────────────────────────────

def parse_mapper_xml(path: str) -> tuple[str, dict[str, list[str]]]:
    """返回 (namespace, {op: [tables]})。op 形如 'insert'/'select'/'update'/'delete'。"""
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return "", {}
    root = tree.getroot()
    ns = root.get("namespace", "")
    ops: dict[str, set] = defaultdict(set)
    for tag in ("select", "insert", "update", "delete"):
        for node in root.iter(tag):
            sql = "".join(node.itertext())
            # 动态 SQL 片段也一起扫 (宁多勿漏)
            for m in SQL_TABLE_RE.finditer(sql):
                tbl = m.group(1)
                if tbl.lower() not in ("select", "where", "set", "values", "dual", "on", "as", "left", "right", "inner", "outer"):
                    ops[tag].add(tbl)
    return ns, {k: sorted(v) for k, v in ops.items()}


# ── 2. MyBatis-Plus / JPA 注解解析 ───────────────────────────────────────────

ANNOT_RE = re.compile(r'@TableName\(\s*(?:value\s*=\s*)?"([\w]+)"')
JPA_TABLE_RE = re.compile(r'@Table\(\s*name\s*=\s*"([\w]+)"')
CLASS_RE = re.compile(r"public\s+(?:class|interface)\s+(\w+)")


def scan_entity_annotations(project: str) -> dict[str, str]:
    """扫描 @TableName/@Table, 返回 {类FQCN或简单名: 表名}。"""
    out: dict[str, str] = {}
    for path in glob.glob(f"examples/{project}/**/*.{os.suffix if False else 'java'}", recursive=True):
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if "@TableName" not in src and "@Table(" not in src:
            continue
        tbl = None
        m = ANNOT_RE.search(src)
        if m:
            tbl = m.group(1)
        else:
            m = JPA_TABLE_RE.search(src)
            if m:
                tbl = m.group(1)
        if not tbl:
            continue
        cm = CLASS_RE.search(src)
        if not cm:
            continue
        cls = cm.group(1)
        # FQCN 从 package 行推断
        pm = re.search(r"package\s+([\w.]+);", src)
        fqcn = f"{pm.group(1)}.{cls}" if pm else cls
        out[fqcn] = tbl
        out[cls] = tbl  # 简单名别名
    return out


# ── 3. callgraph 关联: API → mapper/entity → 表 ──────────────────────────────

def load_callgraph(project: str) -> tuple[dict, dict]:
    """加载 Joern callgraph 缓存。返回 (methods, calls)。兼容两种键名。"""
    for cg in glob.glob(f"{project}-callgraph.json") + glob.glob(f"examples/{project}/*callgraph*.json"):
        try:
            data = json.load(open(cg))
        except (OSError, json.JSONDecodeError):
            continue
        methods = data.get("methods", [])
        calls = data.get("calls", [])
        normalized = []
        for c in calls:
            caller = c.get("caller") or c.get("callerFullName", "")
            callee = c.get("callee") or c.get("calleeFullName", "")
            normalized.append((caller, callee))
        return methods, normalized
    return [], []


def api_entries_for(project: str) -> list[dict]:
    """用 api_discovery 提取 API 入口。"""
    try:
        from api_discovery import discover_api_entries
        entries = discover_api_entries(f"examples/{project}")
        return [{"class_name": e.class_name, "method": e.method,
                 "http_method": e.http_method, "http_path": e.http_path,
                 "src_file": e.src_file, "line_number": e.line_number} for e in entries]
    except Exception as e:
        print(f"  ⚠ {project} api_discovery 失败: {e}")
        return []


def _method_key(full_name: str) -> str:
    """fullName → 'SimpleName.method' 键 (去包名去签名)。
    fullName 形如: org.dromara.system.service.ISysMenuService.checkRouteConfigUnique:boolean(...)
    caller/callee 同格式。"""
    base = full_name.split("(")[0].split(":")[0]
    parts = base.rsplit(".", 2)  # [包..., 类, 方法]
    if len(parts) >= 2:
        return f"{parts[-2]}.{parts[-1]}"
    return base


def build_api_table_map(project: str) -> dict:
    """主流程: 组合 mapper XML + 实体注解 + callgraph, 输出 API → tables。"""
    print(f"── {project} ──")

    # mapper XML: {namespace 简单名: {op: [tables]}}
    xml_map: dict[str, dict[str, list[str]]] = {}
    for x in glob.glob(f"examples/{project}/**/*.xml", recursive=True):
        if "mapper" not in os.path.basename(x).lower() and "Mapper" not in x:
            continue
        ns, ops = parse_mapper_xml(x)
        if ns:
            simple = ns.split(".")[-1]
            xml_map[simple] = ops

    # 实体注解: {类简单名: 表名}
    entities = scan_entity_annotations(project)

    # callgraph
    methods, calls = load_callgraph(project)
    if not methods:
        print(f"  ⚠ 无 callgraph 缓存, 仅输出 API 清单 (无表映射)")
        methods, calls = [], []

    # 邻接表: key(caller) → set(key(callee)); 键统一为 'SimpleName.method'
    adj: dict[str, set] = defaultdict(set)
    for caller, callee in calls:
        adj[_method_key(caller)].add(_method_key(callee))

    # mapper/entity 能触达的表
    def tables_of_node(key: str) -> set[str]:
        tables: set[str] = set()
        cls_name = key.split(".")[0]
        if cls_name in xml_map:
            for op, tbls in xml_map[cls_name].items():
                tables.update(tbls)
        for fq, tbl in entities.items():
            if fq.split(".")[-1] == cls_name:
                tables.add(tbl)
        return tables

    # API 入口处理
    entries = api_entries_for(project)
    result = {}
    for e in entries:
        api_key = f"{e['http_method']} {e['http_path']}"
        ctrl_key = f"{e['class_name'].split('.')[-1]}.{e['method']}"

        # BFS 从 controller 方法向下 6 层, 收集可触达表
        tables: set[str] = set()
        chain: list[str] = []
        seen: set[str] = set()
        frontier = [ctrl_key]
        depth = 0
        while frontier and depth < 6:
            nxt: list[str] = []
            for n in frontier:
                if n in seen:
                    continue
                seen.add(n)
                chain.append(n)
                tables |= tables_of_node(n)
                nxt.extend(adj.get(n, ()))
            frontier = list(dict.fromkeys(nxt))[:80]
            depth += 1

        # ── 兜底①: callgraph 缺 MyBatis-Plus 虚调用边 (baseMapper.insert 等)。
        # 读 Impl 源码: 解析 "private final XxxMapper baseMapper" 字段声明 →
        # XxxMapper 的 XML 表 / XxxMapper 泛型实体 → @TableName 表。
        if not tables:
            src_hint = _find_impl_src(project, chain)
            if src_hint:
                src = open(src_hint, encoding="utf-8", errors="replace").read()
                # 显式 mapper 引用
                for m in re.finditer(r"(\w+Mapper)\.\w+", src):
                    mapper_cls = m.group(1)
                    if mapper_cls in xml_map:
                        for tbls in xml_map[mapper_cls].values():
                            tables.update(tbls)
                # baseMapper.* → Impl 内声明的 mapper 字段类型
                if re.search(r"\bbaseMapper\.", src):
                    mapper_cls = None
                    # 形式1: private final SysMenuMapper baseMapper;
                    fm = re.search(r"(\w+Mapper)\s+baseMapper\s*;", src)
                    if fm:
                        mapper_cls = fm.group(1)
                    else:
                        # 形式2: extends ServiceImpl<SysFileMapper, SysFile> (继承的 baseMapper)
                        gm = re.search(r"ServiceImpl<\s*(\w+Mapper)\s*,\s*\w+", src)
                        if gm:
                            mapper_cls = gm.group(1)
                    if mapper_cls:
                        if mapper_cls in xml_map and xml_map[mapper_cls]:
                            for tbls in xml_map[mapper_cls].values():
                                tables.update(tbls)
                        # Mapper 接口无 XML (MyBatis-Plus 常态) →
                        # 从 extends BaseMapperPlus<实体, Vo> / BaseMapper<实体> 找实体 → @TableName
                        mapper_src = _find_mapper_src(project, mapper_cls)
                        if mapper_src:
                            mtxt = open(mapper_src, encoding="utf-8", errors="replace").read()
                            em = re.search(r"(?:BaseMapperPlus|BaseMapper)<\s*([\w]+)\s*,", mtxt) or \
                                 re.search(r"(?:BaseMapperPlus|BaseMapper)<\s*([\w]+)\s*>", mtxt)
                            if em:
                                entity_cls = em.group(1)
                                if entity_cls in entities:
                                    tables.add(entities[entity_cls])
                                else:
                                    tables.add(_camel_to_snake(entity_cls))

        result[api_key] = {
            "controller": e["class_name"],
            "method": e["method"],
            "tables_rw": sorted(tables),
            "chain": chain[:20],
            "src": f"{e['src_file']}:{e['line_number']}",
        }

    n_with = sum(1 for v in result.values() if v["tables_rw"])
    print(f"  API: {len(result)}, 有表映射: {n_with} ({n_with/len(result)*100:.0f}%)" if result else "  无 API")
    return result


def _camel_to_snake(name: str) -> str:
    """SysMenu → sys_menu (MyBatis-Plus 默认表名策略)。"""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _find_mapper_src(project: str, mapper_cls: str) -> str:
    """找 Mapper 接口源码文件。"""
    hits = glob.glob(f"examples/{project}/**/{mapper_cls}.java", recursive=True)
    return hits[0] if hits else ""


def _find_impl_src(project: str, chain: list[str]) -> str:
    """从 chain 中的类名推 Impl/Mapper 源码文件。
    路径1: 接口 ISysMenuService → SysMenuServiceImpl.java
    路径2: 链路上的任意 Service/Mapper 类 → 直接找同名 Impl 或类文件"""
    if not chain:
        return ""
    for node in chain:
        cls = node.split(".")[0]
        # 路径1: I 开头接口 → 去掉 I 加 Impl
        if cls.startswith("I") and len(cls) > 2:
            impl = cls[1:] + "Impl"
            hits = glob.glob(f"examples/{project}/**/{impl}.java", recursive=True)
            if hits:
                return hits[0]
        # 路径2: 链上节点本身 (如 SysFileService.getFile 的类名 SysFileService)
        # → 尝试 XxxServiceImpl
        if "Service" in cls:
            impl = cls.replace("Service", "ServiceImpl")
            hits = glob.glob(f"examples/{project}/**/{impl}.java", recursive=True)
            if hits:
                return hits[0]
    return ""


def main():
    all_maps: dict[str, dict] = {}
    for p in PROJECTS:
        try:
            all_maps[p] = build_api_table_map(p)
        except Exception as e:
            print(f"  ✗ {p}: {e}")

    with open("api_table_map.json", "w") as f:
        json.dump(all_maps, f, ensure_ascii=False, indent=1)

    total = sum(len(v) for v in all_maps.values())
    with_tbl = sum(1 for v in all_maps.values() for api in v.values() if api["tables_rw"])
    print(f"\n总计: {total} API, {with_tbl} 个有表映射 ({with_tbl/total*100:.0f}%)" if total else "无数据")
    print("已保存: api_table_map.json")


if __name__ == "__main__":
    main()
