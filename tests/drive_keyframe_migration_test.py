#!/usr/bin/env python3
"""Host-side schema contract for Room migration 3 -> 4."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
DATABASE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/db/PotholeDatabase.kt").read_text()
ENTITIES = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/db/Entities.kt").read_text()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def kotlin_trim_indent(value: str) -> str:
    lines = value.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    indent = min((len(line) - len(line.lstrip()) for line in lines if line.strip()), default=0)
    return "\n".join(line[indent:] if line.strip() else "" for line in lines)


def migration_sql() -> list[str]:
    match = re.search(
        r"private val MIGRATION_3_4\b(?P<body>[\s\S]*?)(?=\n\s*fun getDatabase\()",
        DATABASE,
    )
    require(match is not None, "MIGRATION_3_4 was not found")
    calls = re.finditer(
        r'db\.execSQL\(\s*(?:"""(?P<raw>[\s\S]*?)"""\.trimIndent\(\)|'
        r'"(?P<quoted>(?:\\.|[^"\\])*)")\s*\)',
        match.group("body"),
    )
    statements = [
        kotlin_trim_indent(call.group("raw"))
        if call.group("raw") is not None
        else json.loads(f'"{call.group("quoted")}"')
        for call in calls
    ]
    require(statements, "MIGRATION_3_4 contains no executable SQL")
    return statements


def entity_contract() -> tuple[list[tuple[str, str, int, int]], dict[str, tuple[int, tuple[str, ...]]]]:
    class_marker = "data class DriveKeyframeEntity("
    class_start = ENTITIES.find(class_marker)
    entity_start = ENTITIES.rfind("@Entity(", 0, class_start)
    fields_start = class_start + len(class_marker)
    fields_end = ENTITIES.find("\n)", fields_start)
    require(min(class_start, entity_start, fields_end) >= 0,
            "DriveKeyframeEntity contract was not found")
    annotation = ENTITIES[entity_start:class_start]
    fields = ENTITIES[fields_start:fields_end]
    require('tableName = "drive_keyframes"' in annotation,
            "DriveKeyframeEntity table name changed")

    affinities = {"Long": "INTEGER", "Int": "INTEGER", "Boolean": "INTEGER",
                  "String": "TEXT", "Double": "REAL", "Float": "REAL"}
    columns: list[tuple[str, str, int, int]] = []
    primary_key = False
    for raw_line in fields.splitlines():
        line = raw_line.strip()
        if line.startswith("@PrimaryKey"):
            primary_key = True
            continue
        field = re.match(r"val\s+(\w+):\s*(\w+)(\?)?", line)
        if not field:
            continue
        name, kotlin_type, nullable = field.groups()
        require(kotlin_type in affinities, f"unmapped Kotlin type for {name}: {kotlin_type}")
        columns.append((name, affinities[kotlin_type], 0 if nullable else 1,
                        1 if primary_key else 0))
        primary_key = False

    indexes: dict[str, tuple[int, tuple[str, ...]]] = {}
    for index in re.finditer(
        r"Index\(\s*value\s*=\s*\[(?P<columns>[^]]+)]"
        r"(?:\s*,\s*unique\s*=\s*(?P<unique>true|false))?\s*\)",
        annotation,
    ):
        index_columns = tuple(re.findall(r'"([^"\n]+)"', index.group("columns")))
        name = "index_drive_keyframes_" + "_".join(index_columns)
        indexes[name] = (1 if index.group("unique") == "true" else 0, index_columns)

    require(columns and indexes, "DriveKeyframeEntity columns or indexes were not parsed")
    return columns, indexes


def main() -> int:
    expected_columns, expected_indexes = entity_contract()
    statements = migration_sql()
    require(len(statements) == 1 + len(expected_indexes),
            "migration must contain one table and exactly the entity's indexes")

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE v3_sentinel (id INTEGER PRIMARY KEY NOT NULL)")
    connection.execute("PRAGMA user_version = 3")
    require(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'drive_keyframes'"
    ).fetchone() is None, "v3 fixture unexpectedly contains drive_keyframes")
    for statement in statements:
        connection.execute(statement)

    actual_columns = [
        (row[1], row[2].upper(), row[3], row[5])
        for row in connection.execute("PRAGMA table_info('drive_keyframes')")
    ]
    require(actual_columns == expected_columns,
            f"column contract mismatch\nexpected: {expected_columns}\nactual:   {actual_columns}")

    actual_indexes: dict[str, tuple[int, tuple[str, ...]]] = {}
    for _, name, unique, origin, partial in connection.execute(
        "PRAGMA index_list('drive_keyframes')"
    ):
        require(origin == "c" and partial == 0, f"unexpected index metadata for {name}")
        require(re.fullmatch(r"[A-Za-z0-9_]+", name) is not None, f"unsafe index name: {name}")
        indexed_columns = tuple(
            row[2] for row in connection.execute(f'PRAGMA index_info("{name}")')
        )
        actual_indexes[name] = (unique, indexed_columns)
    require(actual_indexes == expected_indexes,
            f"index contract mismatch\nexpected: {expected_indexes}\nactual:   {actual_indexes}")
    require(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'v3_sentinel'"
    ).fetchone() is not None, "migration damaged the existing v3 schema")

    print("DRIVE KEYFRAME MIGRATION 3->4 TEST PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, sqlite3.Error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
